import streamlit as st
import os
import requests
from .proxy_config import render_proxy_configuration
from config.settings import (
    MODEL_CONFIG, TASK_CONFIG, NEO4J_CONFIG, ERROR_MESSAGES
)

# The set_proxy_config function has been moved to components/proxy_config.py

def test_neo4j_connection(url, username, password):
    """Test Neo4j database connection"""
    try:
        from neo4j import GraphDatabase
        driver = GraphDatabase.driver(url, auth=(username, password))
        with driver.session() as session:
            result = session.run("RETURN 1 as test")
            record = result.single()
            if record and record["test"] == 1:
                driver.close()
                return {"success": True, "message": "Connection successful"}
        driver.close()
        return {"success": False, "error": "Connection test failed"}
    except Exception as e:
        return {"success": False, "error": str(e)}

def render_model_settings():
    """Render model settings section"""
    st.subheader("Model Settings")
    
    # Get current example data
    current_example = st.session_state.get("current_example") or {}
    default_model = current_example.get("model", MODEL_CONFIG["default_model"])
    
    # Model name input
    model_name = st.text_input(
        "🤖 Enter your Model",
        value=default_model,
        placeholder="Supports online-models like gpt-4o-mini, deepseek-chat, etc., while also allowing input of a path to use local models.",
        help="Enter model name or path"
    )
    
    # API Key
    api_key = st.text_input(
        "🔑 Enter your API-Key",
        value=MODEL_CONFIG["default_api_key"],
        type="password",
        placeholder="If using a local-model, this field should be left empty.",
        help="Enter your API key"
    )
    # Remove leading and trailing spaces from API key
    api_key = api_key.strip()
    
    # Base URL
    base_url = st.text_input(
        "🔗 Enter your Base-URL",
        value="Default",
        placeholder="If using the default Base-URL or a local-model, this field should be left empty.",
        help="Enter custom base URL if needed"
    )
    # Remove leading and trailing spaces from Base URL
    base_url = base_url.strip()
    
    # Model configuration completion prompt
    st.info("💡 Model will be initialized automatically when you submit a task.")
    
    return model_name, api_key, base_url

def render_task_configuration():
    """Render task configuration section"""
    st.subheader("Task Configuration")
    
    # Get current example data
    current_example = st.session_state.get("current_example") or {}
    
    # Task type selection
    default_task = current_example.get("task", TASK_CONFIG["default_task"])
    task_type = st.selectbox(
        "🎯 Select your Task",
        TASK_CONFIG["supported_tasks"],
        index=TASK_CONFIG["supported_tasks"].index(default_task) if default_task in TASK_CONFIG["supported_tasks"] else 0,
        help="Choose the extraction task type"
    )
    
    # Neo4j configuration - only displayed for Triple task
    neo4j_config = {}
    if task_type == "Triple":
        st.subheader("🗄️ Neo4j Database Configuration")
        neo4j_config["url"] = st.text_input(
            "Neo4j URL",
            value=NEO4J_CONFIG["default_url"],
            help="Neo4j database connection URL",
            key="neo4j_url"
        )
        neo4j_config["username"] = st.text_input(
            "Neo4j Username",
            value=NEO4J_CONFIG["default_username"],
            help="Neo4j database username",
            key="neo4j_username"
        )
        neo4j_config["password"] = st.text_input(
            "Neo4j Password",
            type="password",
            help="Neo4j database password",
            key="neo4j_password"
        )
        neo4j_config["enable_kg_construction"] = st.checkbox(
            "Enable Knowledge Graph Construction",
            value=False,
            help="Automatically build knowledge graph in Neo4j after extraction",
            key="enable_kg_construction"
        )
        
        # Neo4j connection test
        if st.button("🔍 Test Neo4j Connection", key="test_neo4j"):
            test_result = test_neo4j_connection(
                neo4j_config["url"],
                neo4j_config["username"], 
                neo4j_config["password"]
            )
            if test_result["success"]:
                st.success(f"✅ Neo4j connection successful! {test_result['message']}")
            else:
                st.error(f"❌ Neo4j connection failed: {test_result['error']}")
                st.info("💡 Neo4j Connection Tips:")
                st.write("1. Make sure Neo4j database is running")
                st.write("2. Check URL format (e.g., bolt://localhost:7687)")
                st.write("3. Verify username and password")
                st.write("4. Check firewall settings")
                st.write("5. Ensure Neo4j driver is installed: pip install neo4j")
    
    # Mode selection
    default_mode = current_example.get("mode", TASK_CONFIG["default_mode"])
    mode = st.selectbox(
        "🧭 Select your Mode",
        TASK_CONFIG["supported_modes"],
        index=TASK_CONFIG["supported_modes"].index(default_mode) if default_mode in TASK_CONFIG["supported_modes"] else 0,
        help="Choose the extraction mode"
    )
    
    # Custom mode agent configuration
    agent_config = {}
    if mode == "customized":
        st.subheader("Agent Configuration")
        
        agent_config["schema_agent"] = st.selectbox(
            "🤖 Select your Schema-Agent",
            ["Not Required", "get_default_schema", "get_retrieved_schema", "get_deduced_schema"],
            help="Choose schema generation agent"
        )            
        agent_config["extraction_Agent"] = st.selectbox(
            "🤖 Select your Extraction-Agent",
            ["Not Required", "extract_information_direct", "extract_information_with_case"],
            help="Choose extraction agent"
        )
        
        agent_config["reflection_agent"] = st.selectbox(
            "🤖 Select your Reflection-Agent",
            ["Not Required", "reflect_with_case"],
            help="Choose reflection agent"
        )
    
    return task_type, mode, agent_config, neo4j_config

# The render_proxy_configuration function has been moved to components/proxy_config.py

def render_sidebar():
    """Render the complete sidebar"""
    with st.sidebar:
        st.header("⚙️ Configuration")
        
        # Model settings
        model_name, api_key, base_url = render_model_settings()
        
        st.divider()
        
        # Task configuration
        task_type, mode, agent_config, neo4j_config = render_task_configuration()
        
        st.divider()
        
        # Proxy configuration
        render_proxy_configuration()
        
        return {
            "model_name": model_name,
            "api_key": api_key,
            "base_url": base_url,
            "task_type": task_type,
            "mode": mode,
            "agent_config": agent_config,
            "neo4j_config": neo4j_config
        }