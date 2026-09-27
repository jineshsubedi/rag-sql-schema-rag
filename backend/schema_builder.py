"""
schema_builder.py
-----------------
The RAG Ingestion Script for PostgreSQL Schema Extraction and Indexing.

1. Connects to PostgreSQL using DATABASE_URL.
2. Extracts table metadata, columns, data types, primary keys, and foreign keys
   from the 'public' schema using the information_schema.
3. Formats each table's complete schema into a structured document.
4. Generates embeddings using Ollama (nomic-embed-text).
5. Stores the embedded schema documents in a Qdrant vector database.
"""

import os
import sys
import logging
from typing import Dict, List, Any
import psycopg2
from psycopg2.extras import RealDictCursor

# LangChain Document & Embedding Imports with fallback handling
from langchain_core.documents import Document

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

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("schema_builder")

# Environment configuration with production defaults
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://postgres:postgres@host.docker.internal:5432/postgres"
)
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://ollama:11434")
OLLAMA_EMBED_MODEL = os.getenv("OLLAMA_EMBED_MODEL", "nomic-embed-text")
QDRANT_URL = os.getenv("QDRANT_URL", "http://qdrant:6333")
QDRANT_COLLECTION_NAME = os.getenv("QDRANT_COLLECTION_NAME", "schema_rag")


def get_db_connection():
    """Establish a connection to the target PostgreSQL database."""
    logger.info("Connecting to PostgreSQL at: %s", DATABASE_URL.split("@")[-1])
    return psycopg2.connect(DATABASE_URL)


TARGET_SCHEMAS_DEFAULT = [
    s.strip() for s in os.getenv("TARGET_SCHEMA", "ecommerce,public").split(",") if s.strip()
]


def extract_database_schema(target_schemas: List[str] = None) -> List[Document]:
    """
    Extracts tables, columns, data types, primary keys, and foreign keys from the
    specified schemas in PostgreSQL and creates structured LangChain Documents.
    """
    if target_schemas is None:
        target_schemas = TARGET_SCHEMAS_DEFAULT

    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            # 1. Fetch all base tables in target schemas
            cur.execute("""
                SELECT table_schema, table_name
                FROM information_schema.tables
                WHERE table_schema = ANY(%s)
                  AND table_type = 'BASE TABLE'
                ORDER BY table_schema, table_name;
            """, (target_schemas,))
            table_rows = cur.fetchall()
            tables = [row["table_name"] for row in table_rows]

            if not tables:
                logger.warning("No tables found in schemas %s.", target_schemas)
                return []

            logger.info("Discovered %d tables in schemas %s: %s", len(tables), target_schemas, tables)

            # 2. Fetch all column definitions
            cur.execute("""
                SELECT
                    table_schema,
                    table_name,
                    column_name,
                    data_type,
                    is_nullable,
                    column_default
                FROM information_schema.columns
                WHERE table_schema = ANY(%s)
                ORDER BY table_name, ordinal_position;
            """, (target_schemas,))
            columns_by_table: Dict[str, List[Dict[str, Any]]] = {t: [] for t in tables}
            table_to_schema: Dict[str, str] = {row["table_name"]: row["table_schema"] for row in table_rows}
            for row in cur.fetchall():
                tbl = row["table_name"]
                if tbl in columns_by_table:
                    columns_by_table[tbl].append(row)

            # 3. Fetch primary key constraints
            cur.execute("""
                SELECT
                    tc.table_name,
                    kcu.column_name
                FROM information_schema.table_constraints AS tc
                JOIN information_schema.key_column_usage AS kcu
                    ON tc.constraint_name = kcu.constraint_name
                    AND tc.table_schema = kcu.table_schema
                WHERE tc.constraint_type = 'PRIMARY KEY'
                  AND tc.table_schema = ANY(%s)
                ORDER BY tc.table_name, kcu.ordinal_position;
            """, (target_schemas,))
            primary_keys_by_table: Dict[str, List[str]] = {t: [] for t in tables}
            for row in cur.fetchall():
                tbl = row["table_name"]
                if tbl in primary_keys_by_table:
                    primary_keys_by_table[tbl].append(row["column_name"])

            # 4. Fetch foreign key constraints
            cur.execute("""
                SELECT
                    tc.table_name,
                    kcu.column_name,
                    ccu.table_name AS foreign_table_name,
                    ccu.column_name AS foreign_column_name
                FROM information_schema.table_constraints AS tc
                JOIN information_schema.key_column_usage AS kcu
                    ON tc.constraint_name = kcu.constraint_name
                    AND tc.table_schema = kcu.table_schema
                JOIN information_schema.constraint_column_usage AS ccu
                    ON ccu.constraint_name = tc.constraint_name
                    AND ccu.table_schema = tc.table_schema
                WHERE tc.constraint_type = 'FOREIGN KEY'
                  AND tc.table_schema = ANY(%s)
                ORDER BY tc.table_name, kcu.column_name;
            """, (target_schemas,))
            foreign_keys_by_table: Dict[str, List[Dict[str, str]]] = {t: [] for t in tables}
            incoming_foreign_keys_by_table: Dict[str, List[Dict[str, str]]] = {t: [] for t in tables}

            for row in cur.fetchall():
                src_tbl = row["table_name"]
                target_tbl = row["foreign_table_name"]

                if src_tbl in foreign_keys_by_table:
                    foreign_keys_by_table[src_tbl].append({
                        "column": row["column_name"],
                        "foreign_table": target_tbl,
                        "foreign_column": row["foreign_column_name"]
                    })

                if target_tbl in incoming_foreign_keys_by_table:
                    incoming_foreign_keys_by_table[target_tbl].append({
                        "referencing_table": src_tbl,
                        "referencing_column": row["column_name"],
                        "target_column": row["foreign_column_name"]
                    })

        # 5. Format each table into a rich text document for semantic retrieval
        documents: List[Document] = []
        for table_name in tables:
            cols = columns_by_table.get(table_name, [])
            pks = set(primary_keys_by_table.get(table_name, []))
            fks = foreign_keys_by_table.get(table_name, [])
            incoming_fks = incoming_foreign_keys_by_table.get(table_name, [])

            col_descriptions = []
            for col in cols:
                cname = col["column_name"]
                dtype = col["data_type"]
                nullable = "NULL" if col["is_nullable"] == "YES" else "NOT NULL"
                is_pk = "PRIMARY KEY" if cname in pks else None

                modifiers = [dtype, nullable]
                if is_pk:
                    modifiers.insert(0, is_pk)

                col_descriptions.append(f"  - {cname} ({', '.join(modifiers)})")

            fk_descriptions = []
            if fks:
                for fk in fks:
                    fk_descriptions.append(
                        f"  - {fk['column']} references {fk['foreign_table']}({fk['foreign_column']})"
                    )
            else:
                fk_descriptions.append("  - (none)")

            incoming_descriptions = []
            if incoming_fks:
                for ifk in incoming_fks:
                    incoming_descriptions.append(
                        f"  - Referenced by {ifk['referencing_table']}({ifk['referencing_column']}) -> {ifk['target_column']}"
                    )
            else:
                incoming_descriptions.append("  - (none)")

            actual_schema = table_to_schema.get(table_name, "public")
            doc_text = (
                f"Table Name: {table_name}\n"
                f"Schema: {actual_schema}\n"
                f"Columns:\n" + "\n".join(col_descriptions) + "\n"
                f"Primary Keys:\n  - {', '.join(pks) if pks else '(none)'}\n"
                f"Foreign Keys:\n" + "\n".join(fk_descriptions) + "\n"
                f"Relationships & Inbound References:\n" + "\n".join(incoming_descriptions)
            )

            metadata = {
                "table_name": table_name,
                "schema": actual_schema,
                "column_count": len(cols),
                "primary_keys": list(pks)
            }

            documents.append(Document(page_content=doc_text, metadata=metadata))

        logger.info("Successfully assembled %d table schema documents.", len(documents))
        return documents

    except psycopg2.Error as err:
        logger.error("PostgreSQL query execution failed: %s", err)
        raise
    finally:
        if conn:
            conn.close()


def build_and_index_schema(target_schemas: List[str] = None) -> Dict[str, Any]:
    """
    Extracts the database schema and indexes it into the Qdrant vector database
    using Ollama embeddings.
    """
    logger.info("Starting schema extraction and vector indexing workflow...")

    # Extract schema documents
    documents = extract_database_schema(target_schemas=target_schemas)
    if not documents:
        return {
            "status": "warning",
            "message": f"No tables found in schemas {target_schemas or TARGET_SCHEMAS_DEFAULT}. Ingestion aborted.",
            "indexed_tables": []
        }

    # Initialize Ollama Embeddings
    logger.info(
        "Initializing OllamaEmbeddings (model='%s', base_url='%s')",
        OLLAMA_EMBED_MODEL,
        OLLAMA_BASE_URL
    )
    embeddings = OllamaEmbeddings(
        model=OLLAMA_EMBED_MODEL,
        base_url=OLLAMA_BASE_URL
    )

    # Ingest documents into Qdrant
    logger.info(
        "Connecting to Qdrant at '%s', indexing into collection '%s'...",
        QDRANT_URL,
        QDRANT_COLLECTION_NAME
    )

    # Use QdrantVectorStore.from_documents with force_recreate=True to ensure clean, updated schema index
    vector_store = QdrantVectorStore.from_documents(
        documents=documents,
        embedding=embeddings,
        url=QDRANT_URL,
        collection_name=QDRANT_COLLECTION_NAME,
        force_recreate=True
    )

    indexed_table_names = [doc.metadata["table_name"] for doc in documents]
    logger.info("Successfully indexed %d tables into Qdrant: %s", len(indexed_table_names), indexed_table_names)

    return {
        "status": "success",
        "message": f"Successfully indexed {len(indexed_table_names)} tables into Qdrant.",
        "indexed_tables": indexed_table_names
    }


if __name__ == "__main__":
    logger.info("--- Executing Schema Builder Ingestion Script ---")
    try:
        result = build_and_index_schema()
        print("\n[RESULT] Schema ingestion completed successfully:")
        print(f"Status: {result['status']}")
        print(f"Message: {result['message']}")
        print(f"Indexed Tables: {', '.join(result['indexed_tables'])}\n")
    except Exception as exc:
        logger.exception("Schema indexing encountered an error: %s", exc)
        sys.exit(1)
