"""
main.py
-------
FastAPI backend service and Schema-RAG Text-to-SQL Query Engine.

Features:
- Connects to Qdrant vector store and retrieves relevant PostgreSQL schema contexts.
- Uses Ollama (qwen2.5-coder:7b) via LangChain to generate accurate PostgreSQL queries.
- Executes generated queries against the target PostgreSQL host instance via connection pooling.
- Handles database exceptions gracefully and returns structured JSON responses.
- Exposes an ingestion endpoint (/ingest) to index database schemas dynamically.
"""

import os
import re
import decimal
import datetime
import uuid
import logging
from contextlib import asynccontextmanager, contextmanager
from typing import List, Dict, Any, Optional
import time

import psycopg2
from psycopg2.pool import ThreadedConnectionPool
from psycopg2.extras import RealDictCursor

from fastapi import FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

# LangChain Imports with fallback handling
from langchain_core.prompts import PromptTemplate

try:
    from langchain_ollama import OllamaLLM
except ImportError:
    from langchain_community.llms import Ollama as OllamaLLM

try:
    from langchain_ollama import OllamaEmbeddings
except ImportError:
    from langchain_community.embeddings import OllamaEmbeddings

try:
    from langchain_qdrant import QdrantVectorStore
except ImportError:
    try:
        from langchain_community.vectorstores import Qdrant as QdrantVectorStore
    except ImportError:
        from langchain_qdrant import Qdrant as QdrantVectorStore

import qdrant_client

# Local schema builder ingestion utility
from schema_builder import build_and_index_schema

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("schema_rag_api")

# Environment configurations
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://postgres:postgres@host.docker.internal:5432/postgres"
)
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://ollama:11434")
OLLAMA_LLM_MODEL = os.getenv("OLLAMA_LLM_MODEL", "qwen2.5-coder:7b")
OLLAMA_EMBED_MODEL = os.getenv("OLLAMA_EMBED_MODEL", "nomic-embed-text")
QDRANT_URL = os.getenv("QDRANT_URL", "http://qdrant:6333")
QDRANT_COLLECTION_NAME = os.getenv("QDRANT_COLLECTION_NAME", "schema_rag")

DB_MIN_CONNECTIONS = int(os.getenv("DB_MIN_CONNECTIONS", "1"))
DB_MAX_CONNECTIONS = int(os.getenv("DB_MAX_CONNECTIONS", "10"))

# Global connection pool instance
connection_pool: Optional[ThreadedConnectionPool] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifecycle manager for connection pool creation and graceful shutdown."""
    global connection_pool
    logger.info("Initializing PostgreSQL ThreadedConnectionPool (%d-%d connections)...",
                DB_MIN_CONNECTIONS, DB_MAX_CONNECTIONS)
    try:
        connection_pool = ThreadedConnectionPool(
            minconn=DB_MIN_CONNECTIONS,
            maxconn=DB_MAX_CONNECTIONS,
            dsn=DATABASE_URL
        )
        logger.info("Database connection pool established successfully.")
    except Exception as exc:
        logger.warning(
            "Initial connection pool initialization delayed: %s. (Will connect on first request)",
            exc
        )
    yield
    if connection_pool:
        logger.info("Closing all connections in the database pool...")
        connection_pool.closeall()
        logger.info("Connection pool closed.")


app = FastAPI(
    title="Schema-RAG Text-to-SQL API",
    description="Local Text-to-SQL using LangChain, Ollama, Qdrant, and PostgreSQL",
    version="1.0.0",
    lifespan=lifespan
)

# Enable CORS for frontend flexibility
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@contextmanager
def get_db_connection():
    """Context manager for acquiring and safely returning a database connection."""
    global connection_pool
    if connection_pool is None:
        logger.info("Re-attempting connection pool creation...")
        connection_pool = ThreadedConnectionPool(
            minconn=DB_MIN_CONNECTIONS,
            maxconn=DB_MAX_CONNECTIONS,
            dsn=DATABASE_URL
        )

    conn = connection_pool.getconn()
    try:
        yield conn
    finally:
        connection_pool.putconn(conn)


def serialize_db_value(value: Any) -> Any:
    """Helper to convert complex PostgreSQL types to JSON-serializable primitives."""
    if isinstance(value, (datetime.date, datetime.datetime)):
        return value.isoformat()
    if isinstance(value, datetime.time):
        return value.strftime("%H:%M:%S")
    if isinstance(value, decimal.Decimal):
        return float(value) if (value % 1 > 0) else int(value)
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, (bytes, bytearray)):
        return value.hex()
    return value


def sanitize_row(row: Dict[str, Any]) -> Dict[str, Any]:
    """Sanitizes row dictionary values for JSON response serialization."""
    return {k: serialize_db_value(v) for k, v in row.items()}


def extract_sql_query(raw_llm_response: str) -> tuple[str, str]:
    """
    Extracts the clean SQL string from LLM output, parsing out ```sql ... ```
    or generic ``` ... ``` blocks while stripping unwanted whitespace or commentary.
    Also extracts the thinking process if available.
    """
    text = str(raw_llm_response).strip()

    thinking_process = ""
    think_match = re.search(r"<think>\s*(.*?)\s*</think>", text, re.IGNORECASE | re.DOTALL)
    if think_match:
        thinking_process = think_match.group(1).strip()

    # Search for markdown sql block
    text_without_think = re.sub(r"<think>.*?</think>", "", text, flags=re.IGNORECASE | re.DOTALL).strip()
    code_match = re.search(r"```(?:sql)?\s*([\s\S]*?)\s*```", text_without_think, re.IGNORECASE)
    
    if code_match:
        sql = code_match.group(1).strip()
    else:
        # Fallback if model omitted code fences
        sql = text_without_think.strip()

    # Strip any stray wrapping backticks or quotation marks
    sql = sql.strip("`").strip()
    return sql, thinking_process


# ---------------------------------------------------------------------------
# Request and Response Models
# ---------------------------------------------------------------------------
class QueryRequest(BaseModel):
    question: str = Field(
        ...,
        description="The natural language question to convert to SQL",
        example="Show the top 5 customers who placed the most orders"
    )


class QueryResponse(BaseModel):
    question: str
    retrieved_tables: List[str]
    generated_sql: str
    results: List[Dict[str, Any]]
    row_count: int
    success: bool
    error: Optional[str] = None
    execution_time_ms: Optional[float] = None
    thinking_process: Optional[str] = None


class IngestResponse(BaseModel):
    status: str
    message: str
    indexed_tables: List[str]


# ---------------------------------------------------------------------------
# Prompt Template for SQL Generation
# ---------------------------------------------------------------------------
SQL_PROMPT_TEMPLATE = """You are a senior PostgreSQL database architect and SQL expert.
Your task is to write a single, syntactically correct PostgreSQL SQL query that directly answers the user's question, using ONLY the database schema context provided below.

### DATABASE SCHEMA CONTEXT:
{schema_context}

### RULES & CONSTRAINTS:
1. Generate valid PostgreSQL SQL only.
2. Only reference tables and columns that are explicitly defined in the schema context above.
3. When joining tables, use the exact primary key and foreign key relationships shown in the context.
4. Output ONLY the executable SQL query wrapped inside a single ```sql and ``` markdown code block.
5. Do NOT provide explanations, apologies, comments, or prose outside of the ```sql code block.
6. Unless the question explicitly requests data modifications, generate read-only SELECT queries.
7. First, think step-by-step about how to construct the query and wrap your thoughts in <think>...</think> tags.

### USER QUESTION:
{question}

### POSTGRESQL QUERY:"""


# ---------------------------------------------------------------------------
# API Endpoints
# ---------------------------------------------------------------------------
@app.get("/health", tags=["System"])
def health_check():
    """Verify backend health and external service readiness."""
    status_report = {
        "status": "healthy",
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "database_connected": False,
        "qdrant_url": QDRANT_URL,
        "ollama_base_url": OLLAMA_BASE_URL,
        "llm_model": OLLAMA_LLM_MODEL,
        "embed_model": OLLAMA_EMBED_MODEL
    }

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1;")
                status_report["database_connected"] = True
    except Exception as e:
        status_report["database_error"] = str(e)
        status_report["status"] = "degraded"

    return status_report


@app.post("/ingest", response_model=IngestResponse, tags=["Schema Management"])
def trigger_schema_ingestion(schema_name: str = "ecommerce,public"):
    """
    Triggers schema extraction from PostgreSQL and indexes it into Qdrant.
    Can be invoked from the frontend or API client whenever database tables change.
    """
    try:
        target_schemas = [s.strip() for s in schema_name.split(",") if s.strip()]
        result = build_and_index_schema(target_schemas=target_schemas)
        return IngestResponse(
            status=result["status"],
            message=result["message"],
            indexed_tables=result["indexed_tables"]
        )
    except Exception as exc:
        logger.exception("Schema ingestion failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Schema ingestion error: {str(exc)}"
        )


@app.post("/query", response_model=QueryResponse, tags=["Text-to-SQL"])
def process_text_to_sql_query(payload: QueryRequest):
    """
    Main Text-to-SQL pipeline:
    1. Connects to Qdrant with Ollama embeddings.
    2. Performs similarity search to retrieve top 4 most relevant table schemas.
    3. Prompts Qwen2.5-Coder:7b to generate a valid PostgreSQL query.
    4. Executes the query against host PostgreSQL database.
    5. Returns retrieved tables, generated SQL, and raw execution results.
    """
    start_time = time.time()
    question = payload.question.strip()
    if not question:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Question string cannot be empty."
        )

    logger.info("Received query question: '%s'", question)

    # 1. Initialize Ollama Embeddings & Qdrant Client
    try:
        embeddings = OllamaEmbeddings(
            model=OLLAMA_EMBED_MODEL,
            base_url=OLLAMA_BASE_URL
        )
        q_client = qdrant_client.QdrantClient(url=QDRANT_URL)

        if not q_client.collection_exists(QDRANT_COLLECTION_NAME):
            return QueryResponse(
                question=question,
                retrieved_tables=[],
                generated_sql="",
                results=[],
                row_count=0,
                success=False,
                error=(
                    f"Qdrant collection '{QDRANT_COLLECTION_NAME}' does not exist yet. "
                    "Please run schema ingestion via POST /ingest or backend/schema_builder.py first."
                ),
                execution_time_ms=(time.time() - start_time) * 1000
            )

        vector_store = QdrantVectorStore.from_existing_collection(
            embedding=embeddings,
            collection_name=QDRANT_COLLECTION_NAME,
            url=QDRANT_URL
        )
    except Exception as exc:
        logger.exception("Failed to connect to Qdrant vector store: %s", exc)
        return QueryResponse(
            question=question,
            retrieved_tables=[],
            generated_sql="",
            results=[],
            row_count=0,
            success=False,
            error=f"Vector store connection error: {str(exc)}",
            execution_time_ms=(time.time() - start_time) * 1000
        )

    # 2. Retrieve top 4 most relevant schema documents
    try:
        retrieved_docs = vector_store.similarity_search(question, k=4)
        if not retrieved_docs:
            return QueryResponse(
                question=question,
                retrieved_tables=[],
                generated_sql="",
                results=[],
                row_count=0,
                success=False,
                error="No schema documents found in vector store. Please ingest the schema first.",
                execution_time_ms=(time.time() - start_time) * 1000
            )

        retrieved_table_names = [
            doc.metadata.get("table_name", "unknown") for doc in retrieved_docs
        ]
        schema_context = "\n\n---\n\n".join([doc.page_content for doc in retrieved_docs])
        logger.info("Retrieved relevant schema tables: %s", retrieved_table_names)
    except Exception as exc:
        logger.exception("Similarity search in Qdrant failed: %s", exc)
        return QueryResponse(
            question=question,
            retrieved_tables=[],
            generated_sql="",
            results=[],
            row_count=0,
            success=False,
            error=f"Schema retrieval error: {str(exc)}",
            execution_time_ms=(time.time() - start_time) * 1000
        )

    # 3. Initialize Ollama LLM (qwen2.5-coder:7b)
    try:
        llm = OllamaLLM(
            model=OLLAMA_LLM_MODEL,
            base_url=OLLAMA_BASE_URL,
            temperature=0.1
        )

        prompt = PromptTemplate(
            template=SQL_PROMPT_TEMPLATE,
            input_variables=["schema_context", "question"]
        )

        chain = prompt | llm
        logger.info("Invoking LLM (%s) for SQL synthesis...", OLLAMA_LLM_MODEL)
        raw_llm_output = chain.invoke({
            "schema_context": schema_context,
            "question": question
        })
    except Exception as exc:
        logger.exception("LLM generation failed: %s", exc)
        return QueryResponse(
            question=question,
            retrieved_tables=retrieved_table_names,
            generated_sql="",
            results=[],
            row_count=0,
            success=False,
            error=f"LLM generation failed: {str(exc)}",
            execution_time_ms=(time.time() - start_time) * 1000
        )

    # 4. Extract and clean the SQL statement
    sql_query, thinking_process = extract_sql_query(raw_llm_output)
    logger.info("Generated SQL query: %s", sql_query)

    if not sql_query:
        return QueryResponse(
            question=question,
            retrieved_tables=retrieved_table_names,
            generated_sql="",
            results=[],
            row_count=0,
            success=False,
            error="LLM failed to produce a valid SQL query.",
            execution_time_ms=(time.time() - start_time) * 1000,
            thinking_process=thinking_process
        )

    # 5. Execute generated SQL against host PostgreSQL instance
    db_results: List[Dict[str, Any]] = []
    execution_error: Optional[str] = None
    query_success = False

    try:
        with get_db_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(sql_query)
                # If query returns rows (e.g., SELECT or RETURNING)
                if cur.description:
                    raw_rows = cur.fetchall()
                    db_results = [sanitize_row(dict(row)) for row in raw_rows]
                else:
                    conn.commit()
                    db_results = [{
                        "rows_affected": cur.rowcount,
                        "status": "Query executed successfully without returning rows"
                    }]
                query_success = True
    except psycopg2.Error as db_err:
        logger.error("PostgreSQL execution error for query [%s]: %s", sql_query, db_err)
        execution_error = f"Database Execution Error: {db_err.pgerror or str(db_err).strip()}"
    except Exception as general_err:
        logger.exception("Unexpected error during query execution: %s", general_err)
        execution_error = f"Execution Error: {str(general_err)}"

    return QueryResponse(
        question=question,
        retrieved_tables=retrieved_table_names,
        generated_sql=sql_query,
        results=db_results,
        row_count=len(db_results),
        success=query_success,
        error=execution_error,
        execution_time_ms=(time.time() - start_time) * 1000,
        thinking_process=thinking_process
    )
