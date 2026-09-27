"""
app.py
------
Streamlit UI for the Schema-RAG Text-to-SQL system.

Features:
- Clean, modern chat interface using st.chat_message and st.chat_input.
- Communicates with the FastAPI backend (/query and /ingest).
- Keeps the chat thread concise by organizing technical details inside st.expander:
    * Retrieved Schema Tables
    * Generated SQL Query
    * Raw Database Results (rendered as interactive tables and JSON)
- Includes sidebar controls for checking system status, re-indexing schema, and clearing history.
"""

import os
import requests
import streamlit as st
import pandas as pd

# Backend URL configuration (defaults to Docker network host)
BACKEND_URL = os.getenv("BACKEND_URL", "http://backend:8000").rstrip("/")

# Page setup
st.set_page_config(
    page_title="Schema-RAG Text-to-SQL",
    page_icon="🔍",
    layout="wide",
    initial_sidebar_state="expanded"
)

# ---------------------------------------------------------------------------
# Sidebar: System Controls & Information
# ---------------------------------------------------------------------------
with st.sidebar:
    st.title("⚙️ System Console")
    st.caption("Local Schema-RAG Text-to-SQL Engine")

    st.markdown("---")
    st.subheader("Architecture Stack")
    st.markdown(
        """
        - **Target DB:** Host PostgreSQL (`host.docker.internal`)
        - **Vector Store:** Qdrant (`qdrant:6333`)
        - **Embeddings:** Ollama `nomic-embed-text`
        - **LLM:** Ollama `qwen2.5-coder:7b`
        - **Backend:** FastAPI & LangChain
        """
    )

    st.markdown("---")
    st.subheader("Schema Management")
    if st.button("🔄 Sync / Re-index Schema", use_container_width=True):
        with st.spinner("Extracting PostgreSQL schema and indexing into Qdrant..."):
            try:
                resp = requests.post(f"{BACKEND_URL}/ingest", timeout=90)
                if resp.status_code == 200:
                    data = resp.json()
                    st.success(f"Indexed {len(data.get('indexed_tables', []))} tables!")
                    st.write("**Tables:**", ", ".join(data.get("indexed_tables", [])))
                else:
                    st.error(f"Ingest failed ({resp.status_code}): {resp.text}")
            except requests.exceptions.RequestException as e:
                st.error(f"Failed to connect to backend: {e}")

    st.markdown("---")
    st.subheader("Diagnostics")
    if st.button("🩺 Check Backend Health", use_container_width=True):
        try:
            resp = requests.get(f"{BACKEND_URL}/health", timeout=10)
            if resp.status_code == 200:
                health = resp.json()
                st.success("Backend is reachable!")
                st.json(health)
            else:
                st.warning(f"Backend returned status {resp.status_code}")
        except Exception as e:
            st.error(f"Healthcheck error: {e}")

    st.markdown("---")
    if st.button("🗑️ Clear Chat History", use_container_width=True):
        st.session_state.messages = []
        st.rerun()

# ---------------------------------------------------------------------------
# Main Chat Application Interface
# ---------------------------------------------------------------------------
st.title("🔍 Schema-RAG Text-to-SQL")
st.markdown(
    "Ask natural language questions about your PostgreSQL database. "
    "The system retrieves the relevant table schemas using vector search, generates valid SQL with "
    "**Qwen 2.5 Coder**, and executes it safely against your host database."
)

# Initialize chat message history
if "messages" not in st.session_state:
    st.session_state.messages = [
        {
            "role": "assistant",
            "content": "Hello! I am ready to convert your questions into PostgreSQL queries. What would you like to know from your database?",
            "retrieved_tables": [],
            "generated_sql": "",
            "results": [],
            "error": None,
            "success": True,
            "execution_time_ms": None,
            "thinking_process": None
        }
    ]

# Render existing chat history
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

        # Show expandable details only for assistant replies that executed queries
        if msg["role"] == "assistant" and (msg.get("generated_sql") or msg.get("error")):
            if msg.get("execution_time_ms"):
                st.caption(f"⏱️ Execution time: {msg['execution_time_ms']:.2f} ms")

            if msg.get("thinking_process"):
                with st.expander("🧠 Model Thinking Process"):
                    st.write(msg["thinking_process"])

            # 1. Retrieved Tables
            if msg.get("retrieved_tables"):
                with st.expander("📑 Relevant Schema Tables (Retrieved by Vector Search)"):
                    st.write(", ".join([f"`{t}`" for t in msg["retrieved_tables"]]))

            # 2. Generated SQL Query
            if msg.get("generated_sql"):
                with st.expander("💻 Generated PostgreSQL Query", expanded=False):
                    st.code(msg["generated_sql"], language="sql")

            # 3. Database Execution Results or Error
            if msg.get("error"):
                st.error(msg["error"])
            elif msg.get("results") is not None and len(msg.get("results")) > 0:
                with st.expander(f"📊 Query Results ({len(msg['results'])} rows)", expanded=True):
                    results_data = msg["results"]
                    # Render as Pandas DataFrame for a clean tabular view
                    try:
                        df = pd.DataFrame(results_data)
                        st.dataframe(df, use_container_width=True)
                    except Exception:
                        st.json(results_data)
            elif msg.get("success") and not msg.get("error") and msg.get("generated_sql"):
                st.info("Query executed successfully. (0 rows returned)")

# User input prompt
if user_prompt := st.chat_input("E.g., Find the top 10 users registered in the last 30 days"):
    # Append user question to chat history
    st.session_state.messages.append({
        "role": "user",
        "content": user_prompt
    })

    # Render user prompt immediately
    with st.chat_message("user"):
        st.markdown(user_prompt)

    # Process query with backend
    with st.chat_message("assistant"):
        with st.spinner("Analyzing schema, generating SQL, and querying PostgreSQL..."):
            try:
                response = requests.post(
                    f"{BACKEND_URL}/query",
                    json={"question": user_prompt},
                    timeout=120
                )

                if response.status_code == 200:
                    data = response.json()

                    success = data.get("success", False)
                    retrieved_tables = data.get("retrieved_tables", [])
                    generated_sql = data.get("generated_sql", "")
                    results = data.get("results", [])
                    error_msg = data.get("error")
                    execution_time_ms = data.get("execution_time_ms")
                    thinking_process = data.get("thinking_process")

                    if success:
                        row_count = len(results)
                        reply_text = f"Query executed successfully against PostgreSQL ({row_count} row{'s' if row_count != 1 else ''} returned)."
                    else:
                        reply_text = "I encountered an issue executing the generated query."

                    st.markdown(reply_text)

                    if execution_time_ms:
                        st.caption(f"⏱️ Execution time: {execution_time_ms:.2f} ms")

                    if thinking_process:
                        with st.expander("🧠 Model Thinking Process"):
                            st.write(thinking_process)

                    # Expanders for technical details
                    if retrieved_tables:
                        with st.expander("📑 Relevant Schema Tables (Retrieved by Vector Search)"):
                            st.write(", ".join([f"`{t}`" for t in retrieved_tables]))

                    if generated_sql:
                        with st.expander("💻 Generated PostgreSQL Query", expanded=False):
                            st.code(generated_sql, language="sql")

                    if error_msg:
                        st.error(error_msg)
                    elif results:
                        with st.expander(f"📊 Query Results ({len(results)} rows)", expanded=True):
                            try:
                                df = pd.DataFrame(results)
                                st.dataframe(df, use_container_width=True)
                            except Exception:
                                st.json(results)

                    # Save assistant response to session state
                    st.session_state.messages.append({
                        "role": "assistant",
                        "content": reply_text,
                        "retrieved_tables": retrieved_tables,
                        "generated_sql": generated_sql,
                        "results": results,
                        "error": error_msg,
                        "success": success,
                        "execution_time_ms": execution_time_ms,
                        "thinking_process": thinking_process
                    })

                else:
                    error_detail = f"Backend error ({response.status_code}): {response.text}"
                    st.error(error_detail)
                    st.session_state.messages.append({
                        "role": "assistant",
                        "content": "An error occurred while contacting the backend service.",
                        "error": error_detail,
                        "success": False
                    })

            except requests.exceptions.ConnectionError:
                err_text = (
                    f"Could not connect to backend service at `{BACKEND_URL}`. "
                    "Please ensure the Docker containers are running and reachable."
                )
                st.error(err_text)
                st.session_state.messages.append({
                    "role": "assistant",
                    "content": err_text,
                    "error": err_text,
                    "success": False
                })
            except requests.exceptions.Timeout:
                err_text = "The request timed out. Model inference or database execution took longer than expected."
                st.error(err_text)
                st.session_state.messages.append({
                    "role": "assistant",
                    "content": err_text,
                    "error": err_text,
                    "success": False
                })
            except Exception as e:
                err_text = f"Unexpected error: {str(e)}"
                st.error(err_text)
                st.session_state.messages.append({
                    "role": "assistant",
                    "content": err_text,
                    "error": err_text,
                    "success": False
                })
