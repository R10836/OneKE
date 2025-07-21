import streamlit as st
import json
import os
import sys
import tempfile
import random
import re
from pathlib import Path
from typing import Dict, Any, Optional
import streamlit.components.v1 as components
from pyvis.network import Network
import networkx as nx

try:
    from neo4j import GraphDatabase
    NEO4J_AVAILABLE = True
except ImportError:
    NEO4J_AVAILABLE = False

# 代理设置函数 - 支持用户配置
def set_proxy_config(enable_proxy=False, proxy_host="127.0.0.1", proxy_port="7890"):
    """设置代理配置
    
    Args:
        enable_proxy (bool): 是否启用代理
        proxy_host (str): 代理服务器地址
        proxy_port (str): 代理端口
    """
    if enable_proxy:
        proxy_url = f"http://{proxy_host}:{proxy_port}"
        os.environ['http_proxy'] = proxy_url
        os.environ['https_proxy'] = proxy_url
        os.environ['HTTP_PROXY'] = proxy_url
        os.environ['HTTPS_PROXY'] = proxy_url
        os.environ['USE_PROXY'] = 'true'
        print(f"🔧 代理已启用: {proxy_url}")
    else:
        # 清除代理设置
        for key in ['http_proxy', 'https_proxy', 'HTTP_PROXY', 'HTTPS_PROXY', 'USE_PROXY']:
            os.environ.pop(key, None)
        print("❌ 代理已禁用")

# 初始化时不设置代理，等待用户配置
# print("⚙️ 代理配置将由用户在界面中设置")

# 添加OneKE源码路径
oneke_path = Path("./src")
if oneke_path.exists():
    sys.path.insert(0, str(oneke_path))
    
    try:
        from models import *
        from pipeline import Pipeline
        from utils import *
        ONEKE_AVAILABLE = True
        
        # 尝试导入construct模块
        try:
            from construct.convert import generate_cypher_statements, execute_cypher_statements
            CONSTRUCT_AVAILABLE = True
        except ImportError:
            CONSTRUCT_AVAILABLE = False
    except ImportError as e:
        st.error(f"Failed to import OneKE modules: {e}")
        ONEKE_AVAILABLE = False
        CONSTRUCT_AVAILABLE = False
else:
    ONEKE_AVAILABLE = False
    CONSTRUCT_AVAILABLE = False
    st.warning("OneKE source path not found. Using fallback implementations.")

# OneKEProcessor不再需要，直接使用Pipeline


def generate_cypher_from_result(result_str):
    """从抽取结果生成Cypher语句"""
    try:
        if isinstance(result_str, str):
            result_data = json.loads(result_str)
        else:
            result_data = result_str
        
        cypher_statements = []
        
        # 处理OneKE Triple任务的输出格式：{"triple_list": [...]}
        if isinstance(result_data, dict) and 'triple_list' in result_data:
            triple_list = result_data['triple_list']
            for item in triple_list:
                if isinstance(item, dict) and 'head' in item and 'relation' in item and 'tail' in item:
                    head = str(item['head']).replace("'", "\\'")
                    tail = str(item['tail']).replace("'", "\\'")
                    relation = str(item['relation']).replace("'", "\\'")
                    
                    # 使用类型信息（如果可用）
                    head_type = item.get('head_type', 'Entity')
                    tail_type = item.get('tail_type', 'Entity')
                    relation_type = item.get('relation_type', relation)
                    
                    cypher = f"MERGE (h:{head_type} {{name: '{head}'}})"
                    cypher += f"\nMERGE (t:{tail_type} {{name: '{tail}'}})"
                    cypher += f"\nMERGE (h)-[:{relation_type.replace(' ', '_').upper()}]->(t);"
                    cypher_statements.append(cypher)
        
        # 处理简单的三元组列表格式（向后兼容）
        elif isinstance(result_data, list):
            for item in result_data:
                if isinstance(item, dict) and 'head' in item and 'relation' in item and 'tail' in item:
                    head = str(item['head']).replace("'", "\\'")
                    tail = str(item['tail']).replace("'", "\\'")
                    relation = str(item['relation']).replace("'", "\\'")
                    
                    cypher = f"MERGE (h:Entity {{name: '{head}'}})"
                    cypher += f"\nMERGE (t:Entity {{name: '{tail}'}})"
                    cypher += f"\nMERGE (h)-[:{relation.replace(' ', '_').upper()}]->(t);"
                    cypher_statements.append(cypher)
        
        if not cypher_statements:
            return f"// No valid triples found in result. Expected format: {{\"triple_list\": [{{\"head\": \"...\", \"relation\": \"...\", \"tail\": \"...\"}}]}}"
        
        return "\n\n".join(cypher_statements)
    except Exception as e:
        return f"// Error generating Cypher: {str(e)}"

def test_neo4j_connection(neo4j_url, neo4j_username, neo4j_password):
    """测试Neo4j数据库连接"""
    if not NEO4J_AVAILABLE:
        return {"success": False, "error": "Neo4j driver not available. Please install: pip install neo4j"}
    
    try:
        # 验证输入参数
        if not neo4j_url or not neo4j_username or not neo4j_password:
            return {"success": False, "error": "Please provide all connection parameters (URL, username, password)"}
        
        # 尝试连接
        driver = GraphDatabase.driver(neo4j_url, auth=(neo4j_username, neo4j_password))
        
        # 测试连接
        with driver.session() as session:
            result = session.run("RETURN 'Connection successful' as message")
            message = result.single()["message"]
            
            # 获取数据库信息
            db_info = session.run("CALL dbms.components() YIELD name, versions RETURN name, versions[0] as version")
            db_details = db_info.single()
            db_name = db_details["name"] if db_details else "Neo4j"
            db_version = db_details["version"] if db_details else "Unknown"
        
        driver.close()
        return {
            "success": True, 
            "message": f"Connected to {db_name} {db_version}"
        }
    
    except Exception as e:
        error_msg = str(e)
        if "authentication" in error_msg.lower():
            error_msg = "Authentication failed. Please check username and password."
        elif "connection" in error_msg.lower():
            error_msg = "Connection failed. Please check URL and ensure Neo4j is running."
        return {"success": False, "error": error_msg}

def build_knowledge_graph(result_str, neo4j_url, neo4j_username, neo4j_password):
    """构建知识图谱到Neo4j数据库"""
    if not NEO4J_AVAILABLE:
        return {"success": False, "error": "Neo4j driver not available"}
    
    try:
        driver = GraphDatabase.driver(neo4j_url, auth=(neo4j_username, neo4j_password))
        
        cypher_statements = generate_cypher_from_result(result_str)
        if not cypher_statements or cypher_statements.startswith("// Error"):
            return {"success": False, "error": "Failed to generate Cypher statements"}
        
        with driver.session() as session:
            # 执行Cypher语句
            for statement in cypher_statements.split("\n\n"):
                if statement.strip():
                    session.run(statement)
            
            # 获取统计信息
            node_count = session.run("MATCH (n) RETURN count(n) as count").single()["count"]
            rel_count = session.run("MATCH ()-[r]->() RETURN count(r) as count").single()["count"]
            
            stats = f"Nodes: {node_count}\nRelationships: {rel_count}"
        
        driver.close()
        return {"success": True, "stats": stats}
    
    except Exception as e:
        return {"success": False, "error": str(e)}

# OneKE webui.py中的示例数据
examples = [
    {
        "task": "Base",
        "mode": "quick",
        "use_file": False,
        "text": "合力治堵!济南交通部门在拥堵路段定点研究交通治理方案",
        "instruction": "请帮我抽取这个新闻事件",
        "constraint": "",
        "output_schema": '{"type": "object", "properties": {"events": {"type": "array", "items": {"type": "object", "properties": {"event_name": {"type": "string"}, "participants": {"type": "array"}, "location": {"type": "string"}}}}}}',
        "file_path": None,
        "update_case": False,
        "truth": "",
    },
    {
        "task": "NER",
        "mode": "quick",
        "use_file": False,
        "text": "Finally, every other year , ELRA organizes a major conference LREC , the International Language Resources and Evaluation Conference .",
        "instruction": "",
        "constraint": '["algorithm", "conference", "else", "product", "task", "field", "metrics", "organization", "researcher", "program language", "country", "location", "person", "university"]',
        "file_path": None,
        "update_case": False,
        "truth": "",
    },
    {
        "task": "RE",
        "mode": "quick",
        "use_file": False,
        "text": "The aid group Doctors Without Borders said that since Saturday , more than 275 wounded people had been admitted and treated at Donka Hospital in the capital of Guinea , Conakry .",
        "instruction": "",
        "constraint": '["nationality", "country capital", "place of death", "children", "location contains", "place of birth", "place lived", "administrative division of country", "country of administrative divisions", "company", "neighborhood of", "company founders"]',
        "file_path": None,
        "update_case": True,
        "truth": """{"relation_list": [{"head": "Guinea", "tail": "Conakry", "relation": "country capital"}]}""",
    },
    {
        "task": "EE",
        "mode": "standard",
        "use_file": False,
        "text": "The file suggested to the user contains no software related to video streaming and simply carries the malicious payload that later compromises victim \u2019s account and sends out the deceptive messages to all victim \u2019s contacts .",
        "instruction": "",
        "constraint": '{"phishing": ["damage amount", "attack pattern", "tool", "victim", "place", "attacker", "purpose", "trusted entity", "time"], "data breach": ["damage amount", "attack pattern", "number of data", "number of victim", "tool", "compromised data", "victim", "place", "attacker", "purpose", "time"], "ransom": ["damage amount", "attack pattern", "payment method", "tool", "victim", "place", "attacker", "price", "time"], "discover vulnerability": ["vulnerable system", "vulnerability", "vulnerable system owner", "vulnerable system version", "supported platform", "common vulnerabilities and exposures", "capabilities", "time", "discoverer"], "patch vulnerability": ["vulnerable system", "vulnerability", "issues addressed", "vulnerable system version", "releaser", "supported platform", "common vulnerabilities and exposures", "patch number", "time", "patch"]}',
        "file_path": None,
        "update_case": False,
        "truth": "",
    },
    {
        "task": "Triple",
        "mode": "quick",
        "use_file": True,
        "file_path": "./data/input_files/Artificial_Intelligence_Wikipedia.txt",
        "instruction": "",
        "constraint": '[["Person", "Place", "Event", "property"], ["Interpersonal", "Located", "Ownership", "Action"]]',
        "text": "",
        "update_case": False,
        "truth": "",
    },
    {
        "task": "Base",
        "mode": "quick",
        "use_file": True,
        "file_path": "./data/input_files/Harry_Potter_Chapter1.pdf",
        "instruction": "Extract main characters and the background setting from this chapter.",
        "constraint": "",
        "output_schema": '{"type": "object", "properties": {"characters": {"type": "array", "items": {"type": "string"}}, "setting": {"type": "object", "properties": {"location": {"type": "string"}, "time_period": {"type": "string"}}}}}',
        "text": "",
        "update_case": False,
        "truth": "",
    },
    {
        "task": "Base",
        "mode": "quick",
        "use_file": True,
        "file_path": "./data/input_files/Tulsi_Gabbard_News.html",
        "instruction": "Extract key information from the given text.",
        "constraint": "",
        "output_schema": '{"type": "object", "properties": {"key_information": {"type": "array", "items": {"type": "object", "properties": {"type": {"type": "string"}, "value": {"type": "string"}, "importance": {"type": "string"}}}}}}',
        "text": "",
        "update_case": False,
        "truth": "",
    },
    {
        "task": "Base",
        "mode": "quick",
        "use_file": False,
        "text": "John Smith, a 45-year-old male, presents with persistent headaches that have lasted for the past 10 days. The headaches are described as moderate and occur primarily in the frontal region, often accompanied by mild nausea. The patient reports no significant medical history except for seasonal allergies, for which he occasionally takes antihistamines. Physical examination reveals a heart rate of 78 beats per minute, blood pressure of 125/80 mmHg, and normal temperature. A neurological examination showed no focal deficits. A CT scan of the head was performed, which revealed no acute abnormalities, and a sinus X-ray suggested mild sinusitis. Based on the clinical presentation and imaging results, the diagnosis is sinusitis, and the patient is advised to take decongestants and rest for recovery.",
        "instruction": "Please extract the key medical information from this case description.",
        "constraint": "",
        "output_schema": '{"type": "object", "properties": {"patient_info": {"type": "object"}, "symptoms": {"type": "array"}, "diagnosis": {"type": "string"}, "treatment": {"type": "array"}}}',
        "file_path": None,
        "update_case": False,
        "truth": "",
    },
    {
        "task": "Base",
        "mode": "quick",
        "use_file": False,
        "text": "张三，男，60岁，主诉背部酸痛已持续约两周，伴有轻微的头晕。患者有高血压病史，已服用降压药物多年，且控制良好；此外，患者曾在五年前接受过一次胆囊切除手术。体检时，心率为75次/分钟，血压为130/85 mmHg。背部触诊时无明显压痛，但活动时出现轻微不适。胸部X光显示无异常，腰部CT检查提示轻度腰椎退行性变。经医生诊断，患者被认为是由于长时间的不良姿势引起的腰椎退行性病变，建议进行物理治疗，并配合止痛药物。",
        "instruction": "请从这个病例描述中，提取出重要的医疗信息",
        "constraint": "",
        "output_schema": '{"type": "object", "properties": {"患者信息": {"type": "object"}, "症状": {"type": "array"}, "诊断": {"type": "string"}, "治疗方案": {"type": "array"}}}',
        "file_path": None,
        "update_case": False,
        "truth": "",
    },
    {
        "task": "Base",
        "mode": "quick",
        "use_file": False,
        "text": "中国政府近日宣布了一项新的环保政策，旨在减少工业污染，并改善空气质量。此次政策将在全国范围内实施，涉及多个行业，尤其是钢铁和煤炭行业。环保部门负责人表示，这项政策的实施标志着中国环保工作的新阶段，预计将在未来五年内显著改善空气质量。",
        "instruction": "请从这段新闻描述中提取出重要的事件信息，包括事件名称、时间、参与人员、事件目的、实施过程及预期结果。",
        "constraint": "",
        "output_schema": '{"type": "object", "properties": {"事件名称": {"type": "string"}, "时间": {"type": "string"}, "参与人员": {"type": "array"}, "事件目的": {"type": "string"}, "实施过程": {"type": "string"}, "预期结果": {"type": "string"}}}',
        "file_path": None,
        "update_case": False,
        "truth": "",
    }
]

def get_model_category(model_name_or_path):
    """获取模型类别，复制自webui.py"""
    if model_name_or_path in ["gpt-3.5-turbo", "gpt-4o-mini", "gpt-4o", "o3-mini"]:
        return ChatGPT
    elif model_name_or_path in ["deepseek-chat", "deepseek-reasoner"]:
        return DeepSeek
    elif re.search(r'(?i)llama', model_name_or_path):
        return LLaMA
    elif re.search(r'(?i)qwen', model_name_or_path):
        return Qwen
    elif re.search(r'(?i)minicpm', model_name_or_path):
        return MiniCPM
    elif re.search(r'(?i)chatglm', model_name_or_path):
        return ChatGLM
    else:
        return BaseEngine

def start_with_example():
    """随机选择一个示例，复制自webui.py"""
    example_index = random.randint(-3, len(examples) - 1)
    example_index = max(example_index, 0)
    example = examples[example_index]
    
    if example_index == 0:
        with open("./data/input_files/ChineseNewsExample.json", "r", encoding="utf-8") as file:
            lines = file.readlines()
            random_line = random.choice(lines).strip()
            try:
                json_data = json.loads(random_line)
                title = json_data.get("title", "No title found")
            except json.JSONDecodeError:
                title = "Error decoding JSON"
            example["text"] = title
    
    return example



# 页面配置
st.set_page_config(
    page_title="OneKE Information Extraction",
    page_icon="🧠",
    layout="wide",
    initial_sidebar_state="expanded"
)

# 初始化session state
if "extraction_results" not in st.session_state:
    st.session_state.extraction_results = None
if "current_example" not in st.session_state:
    st.session_state.current_example = {}

def main():
    """主应用函数"""
    
    # 页面标题和描述 - 基于OneKE项目的Streamlit前端
    # 原OneKE项目信息（已注释）:
    # OneKE: A Dockerized Schema-Guided LLM Agent-based Knowledge Extraction System
    # 🌐Home: http://oneke.openkg.cn/
    # 📹Video: http://oneke.openkg.cn/demo.mp4
    
    st.markdown("""
    <div style="text-align:center;">
        <h1>OneKE-Streamlit-Frontend</h1>
        <p style="font-size: 18px; color: #666; margin-bottom: 10px;">
            基于OneKE项目的Streamlit知识抽取前端界面
        </p>
        <p>
        📝<a href="https://arxiv.org/abs/2412.20005v2" target="_blank">OneKE Paper</a> |
        💻<a href="https://github.com/zjunlp/OneKE" target="_blank">OneKE Code</a>
        </p>
    </div>
    """, unsafe_allow_html=True)
    
    # 随机示例按钮
    col_example1, col_example2, col_example3 = st.columns([1, 2, 1])
    with col_example2:
        if st.button("🎲 Quick Start with an Example 🎲", type="primary", use_container_width=True):
            example = start_with_example()
            st.session_state.current_example = example
            st.rerun()
    
    # 侧边栏配置
    with st.sidebar:
        st.header("⚙️ Configuration")
        
        # 模型配置
        st.subheader("Model Settings")
        
        # 模型名称输入
        current_example = st.session_state.get("current_example") or {}
        default_model = current_example.get("model", "deepseek-chat")
        model_name = st.text_input(
            "🤖 Enter your Model",
            value=default_model,
            placeholder="Supports online-models like gpt-4o-mini, deepseek-chat, etc., while also allowing input of a path to use local models.",
            help="Enter model name or path"
        )
        
        # API Key
        api_key = st.text_input(
            "🔑 Enter your API-Key",
            value="sk-76c999869dcc4a348cc627ce632fa7d0",
            type="password",
            placeholder="If using a local-model, this field should be left empty.",
            help="Enter your API key"
        )
        # 去除API key前后的空格
        api_key = api_key.strip()
        
        # Base URL
        base_url = st.text_input(
            "🔗 Enter your Base-URL",
            value="Default",
            placeholder="If using the default Base-URL or a local-model, this field should be left empty.",
            help="Enter custom base URL if needed"
        )
        # 去除Base URL前后的空格
        base_url = base_url.strip()
        
        # 模型配置完成提示
        st.info("💡 Model will be initialized automatically when you submit a task.")
        
        st.divider()
        
        # 任务和模式配置
        st.subheader("Task Configuration")
        
        # 任务类型选择
        default_task = current_example.get("task", "Base")
        task_type = st.selectbox(
            "🎯 Select your Task",
            ["Base", "NER", "RE", "EE", "Triple"],
            index=["Base", "NER", "RE", "EE", "Triple"].index(default_task) if default_task in ["Base", "NER", "RE", "EE", "Triple"] else 0,
            help="Choose the extraction task type"
        )
        
        # Neo4j配置 - 仅在Triple任务时显示
        if task_type == "Triple":
            st.subheader("🗄️ Neo4j Database Configuration")
            neo4j_url = st.text_input(
                "Neo4j URL",
                value="neo4j://127.0.0.1:7687",
                help="Neo4j database connection URL",
                key="neo4j_url"
            )
            neo4j_username = st.text_input(
                "Neo4j Username",
                value="neo4j",
                help="Neo4j database username",
                key="neo4j_username"
            )
            neo4j_password = st.text_input(
                "Neo4j Password",
                type="password",
                help="Neo4j database password",
                key="neo4j_password"
            )
            enable_kg_construction = st.checkbox(
                "Enable Knowledge Graph Construction",
                value=False,
                help="Automatically build knowledge graph in Neo4j after extraction",
                key="enable_kg_construction"
            )
            
            # Neo4j连接测试
            if st.button("🔍 Test Neo4j Connection", key="test_neo4j"):
                test_result = test_neo4j_connection(
                    neo4j_url,
                    neo4j_username, 
                    neo4j_password
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
        
        # 模式选择
        default_mode = current_example.get("mode", "quick")
        mode = st.selectbox(
            "🧭 Select your Mode",
            ["quick", "standard", "customized"],
            index=["quick", "standard", "customized"].index(default_mode) if default_mode in ["quick", "standard", "customized"] else 0,
            help="Choose the extraction mode"
        )
        
        # 自定义模式的代理配置
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
        
        st.divider()
        
        # 🌐 Proxy Configuration
        with st.expander("🌐 Proxy Configuration", expanded=False):
            st.markdown("**Configure proxy settings for better model downloading from Hugging Face**")
            
            # 启用代理复选框
            enable_proxy = st.checkbox(
                "Enable Proxy",
                value=st.session_state.get('proxy_enabled', False),
                help="Enable proxy for network requests"
            )
            
            # 代理地址和端口输入
            col_proxy1, col_proxy2 = st.columns(2)
            with col_proxy1:
                proxy_host = st.text_input(
                    "Proxy Host",
                    value=st.session_state.get('proxy_host', ''),
                    placeholder="e.g., 127.0.0.1",
                    help="Enter proxy server address"
                )
            
            with col_proxy2:
                proxy_port = st.text_input(
                    "Proxy Port",
                    value=st.session_state.get('proxy_port', ''),
                    placeholder="e.g., 7890",
                    help="Enter proxy server port"
                )
            
            # 应用代理设置按钮
            if st.button("Apply Proxy Settings", key="apply_proxy"):
                if enable_proxy and proxy_host and proxy_port:
                    try:
                        set_proxy_config(proxy_host, proxy_port)
                        st.session_state['proxy_enabled'] = True
                        st.session_state['proxy_host'] = proxy_host
                        st.session_state['proxy_port'] = proxy_port
                        st.success(f"✅ Proxy enabled: {proxy_host}:{proxy_port}")
                    except Exception as e:
                        st.error(f"❌ Failed to set proxy: {str(e)}")
                elif not enable_proxy:
                    try:
                        # 禁用代理
                        if 'http_proxy' in os.environ:
                            del os.environ['http_proxy']
                        if 'https_proxy' in os.environ:
                            del os.environ['https_proxy']
                        st.session_state['proxy_enabled'] = False
                        st.success("✅ Proxy disabled")
                    except Exception as e:
                        st.error(f"❌ Failed to disable proxy: {str(e)}")
                else:
                    st.warning("⚠️ Please provide both proxy host and port")
            
            # 显示当前代理状态
            if st.session_state.get('proxy_enabled', False):
                current_host = st.session_state.get('proxy_host', '')
                current_port = st.session_state.get('proxy_port', '')
                st.info(f"🌐 Current proxy: {current_host}:{current_port}")
            else:
                st.info("🌐 Proxy: Disabled")
            
            # 测试代理连接按钮
            if st.button("Test Proxy Connection", key="test_proxy"):
                if st.session_state.get('proxy_enabled', False):
                    with st.spinner("Testing proxy connection..."):
                        try:
                            # 测试连接到一个简单的网站
                            response = requests.get('https://httpbin.org/ip', timeout=10)
                            if response.status_code == 200:
                                st.success("✅ Proxy connection successful!")
                                st.json(response.json())
                            else:
                                st.error(f"❌ Proxy test failed with status code: {response.status_code}")
                        except Exception as e:
                            st.error(f"❌ Proxy connection failed: {str(e)}")
                else:
                    st.warning("⚠️ Please enable and configure proxy first")
        

    
    # 主内容区域
    col1, col2 = st.columns([1, 2])
    
    with col1:
        st.header("📝 Input Configuration")
        
        # 输入方式选择
        default_use_file = current_example.get("use_file", False)
        use_file = st.checkbox(
            "📂 Use File",
            value=default_use_file,
            help="Choose between file upload or text input"
        )
        
        # 文件上传或文本输入
        input_text = ""
        uploaded_file = None
        example_file_loaded = False
        
        if use_file:
            # 检查是否有示例文件需要加载
            example_file_path = current_example.get("file_path")
            if example_file_path and os.path.exists(example_file_path):
                # 显示示例文件信息
                st.info(f"📁 Example file loaded: {os.path.basename(example_file_path)}")
                st.info("📄 File will be processed by OneKE backend")
                input_text = f"[File: {os.path.basename(example_file_path)}]"
                
                # 标记示例文件已加载
                example_file_loaded = True
            
            # 如果没有加载示例文件，显示文件上传器
            if not example_file_loaded:
                uploaded_file = st.file_uploader(
                    "📖 Upload a File",
                    type=["txt", "pdf", "docx", "html", "json"],
                    help="Upload a text file, PDF, Word document, HTML file, or JSON file"
                )
            
            if uploaded_file is not None:
                # 所有文件都交给OneKE后端处理
                st.success(f"✅ Uploaded {uploaded_file.name} - will be processed by OneKE backend")
                input_text = f"[File uploaded: {uploaded_file.name}]"
            else:
                input_text = ""
        else:
            # 文本输入
            default_text = current_example.get("text", "")
            input_text = st.text_area(
                "📖 Text",
                value=default_text,
                height=200,
                placeholder="Enter your Text please.",
                help="Paste or type the text for information extraction"
            )
        
        if task_type == "Base":
            # Base任务显示instruction和output_schema输入
            default_instruction = current_example.get("instruction", "")
            instruction = st.text_area(
                "🕹️ Instruction",
                value=default_instruction,
                height=100,
                placeholder="You can enter any type of information you want to extract here, for example: Please help me extract all the person names.",
                help="Provide specific instructions for the extraction task"
            )
            
            default_output_schema = current_example.get("output_schema", "")
            output_schema = st.text_area(
                "📋 Output Schema (Optional)",
                value=default_output_schema,
                height=80,
                placeholder='Custom output schema, e.g., {"type": "object", "properties": {"entities": {"type": "array"}}}',
                help="Define custom output schema for Base tasks. Leave empty to use default schema."
            )
            
            # Base任务constraint强制为空
            constraint = ""
        else:
            # 其他任务只显示constraint输入，instruction使用预设值
            default_constraint = current_example.get("constraint", "")
            
            # 为不同任务类型提供不同的约束格式提示
            if task_type == "NER":
                constraint_placeholder = 'Enter entity types as a list, e.g., ["Person", "Location", "Organization"]'
                constraint_help = "Define entity types for Named Entity Recognition. Format: list of strings"
            elif task_type == "RE":
                constraint_placeholder = 'Enter relation types as a list, e.g., ["nationality", "country capital", "born in"]'
                constraint_help = "Define relation types for Relation Extraction. Format: list of strings"
            elif task_type == "EE":
                constraint_placeholder = 'Enter event schema as a dictionary, e.g., {"Conflict": ["Attacker", "Target", "Place"]}'
                constraint_help = "Define event schema for Event Extraction. Format: dictionary with event types as keys and argument roles as values"
            else:  # Triple
                constraint_placeholder = 'Enter constraints for Triple extraction'
                constraint_help = "Define constraints for Triple extraction"
            
            constraint = st.text_area(
                "🕹️ Constraint",
                value=default_constraint,
                height=100,
                placeholder=constraint_placeholder,
                help=constraint_help
            )
            
            # 其他任务instruction和output_schema使用预设值
            instruction = ""
            output_schema = ""
        
        # 更新案例选项
        default_update_case = current_example.get("update_case", False)
        update_case = st.checkbox(
            "💰 Update Case",
            value=default_update_case,
            help="Enable case updates for improved extraction"
        )
        
        # 真值输入（仅在更新案例时显示）
        truth = ""
        if update_case:
            default_truth = current_example.get("truth", "")
            truth = st.text_area(
                "🪙 Truth",
                value=default_truth,
                height=80,
                placeholder='You can enter the truth you want LLM know, for example: {"relation_list": [{"head": "Guinea", "tail": "Conakry", "relation": "country capital"}]}',
                help="Provide ground truth information for case updates"
            )
        
        # 执行抽取按钮
        if st.button("🚀 Submit", type="primary"):
            with st.spinner(f"Performing {task_type} extraction in {mode} mode..."):
                try:
                    # 按照webui.py的submit函数逻辑重新创建Pipeline
                    ModelClass = get_model_category(model_name)
                    if base_url == "Default" or base_url == "":
                        if api_key == "":
                            pipeline = Pipeline(ModelClass(model_name_or_path=model_name))
                        else:
                            pipeline = Pipeline(ModelClass(model_name_or_path=model_name, api_key=api_key))
                    else:
                        if api_key == "":
                            pipeline = Pipeline(ModelClass(model_name_or_path=model_name, base_url=base_url))
                        else:
                            pipeline = Pipeline(ModelClass(model_name_or_path=model_name, api_key=api_key, base_url=base_url))
                    
                    # 根据任务类型处理参数（遵循原始OneKE设计）
                    if task_type == "Base":
                        # Base任务：使用instruction，constraint强制为空
                        instruction = instruction
                        constraint = ""
                    else:
                        # 其他任务：使用constraint，instruction强制为空（使用config中的预设值）
                        instruction = ""
                        constraint = constraint
                    
                    schema_agent = agent_config.get("schema_agent", "Not Required") if mode == "customized" and agent_config else "Not Required"
                    extraction_Agent = agent_config.get("extraction_Agent", "Not Required") if mode == "customized" and agent_config else "Not Required"
                    reflection_agent = agent_config.get("reflection_agent", "Not Required") if mode == "customized" and agent_config else "Not Required"
                    
                    # 按照webui.py的逻辑构建agent3字典
                    agent3 = {}
                    if mode == "customized":
                        if schema_agent not in ["", "Not Required"]:
                            agent3["schema_agent"] = schema_agent
                        if extraction_Agent not in ["", "Not Required"]:
                            agent3["extraction_agent"] = extraction_Agent
                        if reflection_agent not in ["", "Not Required"]:
                            agent3["reflection_agent"] = reflection_agent
                    
                    # 按照webui.py的逻辑处理text和file_path参数
                    if use_file:
                        text_param = ""
                        # 检查是否使用示例文件
                        example_file_path = current_example.get("file_path")
                        if example_file_path and os.path.exists(example_file_path):
                            # 使用示例文件路径
                            file_path_param = example_file_path
                        elif uploaded_file is not None:
                            # 对于Streamlit，我们需要处理上传的文件
                            # 根据文件类型确定后缀名
                            file_extension = os.path.splitext(uploaded_file.name)[1]
                            if not file_extension:
                                file_extension = '.txt'
                            
                            # 保存上传的文件到临时位置
                            with tempfile.NamedTemporaryFile(mode='wb', delete=False, suffix=file_extension) as tmp_file:
                                # 重置文件指针到开始位置
                                uploaded_file.seek(0)
                                tmp_file.write(uploaded_file.read())
                                file_path_param = tmp_file.name
                        else:
                            file_path_param = None
                    else:
                        text_param = input_text
                        file_path_param = None
                    
                    if not update_case:
                        truth = ""
                    
                    # 使用Pipeline的get_extract_result方法，与webui.py保持一致
                    _, _, ger_frontend_schema, ger_frontend_res = pipeline.get_extract_result(
                        task=task_type,
                        text=text_param,
                        use_file=use_file,
                        file_path=file_path_param,
                        instruction=instruction,
                        constraint=constraint,
                        mode=mode,
                        three_agents=agent3,
                        isgui=True,
                        update_case=update_case,
                        truth=truth,
                        output_schema=output_schema,
                        show_trajectory=False,
                    )
                    
                    # 按照webui.py的逻辑处理结果
                    ger_frontend_schema = str(ger_frontend_schema)
                    ger_frontend_res = json.dumps(ger_frontend_res, ensure_ascii=False, indent=4) if isinstance(ger_frontend_res, dict) else str(ger_frontend_res)
                    
                    result = {
                        "success": True,
                        "schema": ger_frontend_schema,
                        "result": ger_frontend_res
                    }
                    st.session_state.extraction_results = result
                    st.success(f"Extraction completed successfully in {mode} mode!")
                    
                    # 清理临时文件（但不删除示例文件）
                    if use_file and file_path_param and os.path.exists(file_path_param):
                        # 只删除临时文件，不删除示例文件
                        example_file_path = current_example.get("file_path")
                        if file_path_param != example_file_path:
                            try:
                                os.unlink(file_path_param)
                            except:
                                pass
                
                except Exception as e:
                    # 参考webui.py的错误处理方式
                    error_message = f"⚠️ Error:\n {str(e)}"
                    result = {
                        "success": False,
                        "error": error_message
                    }
                    st.session_state.extraction_results = result
                    st.error(f"Extraction failed: {str(e)}")
                    
                    # 提供连接错误的具体建议
                    if "Connection error" in str(e) or "connection" in str(e).lower():
                        st.warning("💡 Connection Error Solutions:")
                        st.write("1. Check network connection")
                        st.write("2. Verify API key is correct")
                        st.write("3. Confirm Base URL settings")
                        st.write("4. Try disabling proxy settings")
                        st.write("5. Check firewall settings")
                    
                    # 显示详细错误信息用于调试
                    with st.expander("Detailed Error Information"):
                        st.code(str(e))
        
        
        # 清除按钮 - 与webui.py的clear_all行为一致
        if st.button("🧹 Clear All"):
            # 重置extraction_results和current_example
            st.session_state.extraction_results = None
            st.session_state.current_example = {}
            st.rerun()
    
    with col2:
        st.header("📊 Results")
        
        if st.session_state.extraction_results:
            result = st.session_state.extraction_results
            
            if result.get("success"):
                # 按照webui.py的格式显示结果
                st.markdown("""
                <div style="width: 100%; text-align: center; font-size: 16px; font-weight: bold; position: relative; margin: 20px 0;">
                    <span style="position: absolute; left: 0; top: 50%; transform: translateY(-50%); width: 45%; border-top: 1px solid #ccc;"></span>
                    <span style="position: relative; z-index: 1; background-color: white; padding: 0 10px;">Output:</span>
                    <span style="position: absolute; right: 0; top: 50%; transform: translateY(-50%); width: 45%; border-top: 1px solid #ccc;"></span>
                </div>
                """, unsafe_allow_html=True)
                
                # 创建选项卡来切换不同的视图
                if task_type == "Triple":
                    # Triple任务显示两个选项卡：结果和知识图谱
                    tab1, tab2 = st.tabs(["📄 Schema & Results", "🕸️ Knowledge Graph"])
                    
                    with tab1:
                        # 显示Schema和Results
                        col_schema, col_result = st.columns([1, 1.5])
                        
                        with col_schema:
                            st.markdown("**🤔 Generated Schema**")
                            schema_content = result.get("schema", "")
                            st.code(schema_content, language="python", line_numbers=False)
                            
                            # 下载按钮
                            if schema_content:
                                st.download_button(
                                    label="📥 Download Schema",
                                    data=schema_content,
                                    file_name="generated_schema.json",
                                    mime="application/json",
                                    key="download_schema"
                                )
                        
                        with col_result:
                            st.markdown("**😉 Final Answer**")
                            result_content = result.get("result", "")
                            st.code(result_content, language="json", line_numbers=False)
                            
                            # 下载按钮
                            if result_content:
                                st.download_button(
                                    label="📥 Download Result",
                                    data=result_content,
                                    file_name="final_answer.json",
                                    mime="application/json",
                                    key="download_result"
                                )
                    
                    with tab2:
                        # 知识图谱可视化选项卡
                        st.success("✅ Triple task detected - Knowledge Graph features are available!")
                        
                        # 生成知识图谱可视化
                        html_content, viz_stats = create_knowledge_graph_visualization(result.get("result", ""))
                        
                        # 控制按钮区域
                        button_col1, button_col2, button_col3, button_col4 = st.columns([1, 1, 1, 1])
                        
                        with button_col1:
                            # 显示图谱统计信息
                            if html_content:
                                st.info(f"📊 {viz_stats}")
                            else:
                                st.error("❌ No graph data")
                        
                        with button_col2:
                            if st.button("📄 Download Cypher", key="download_cypher", help="Download Cypher statements"):
                                cypher_statements = generate_cypher_from_result(result.get("result", ""))
                                if cypher_statements:
                                    st.download_button(
                                        label="💾 Save Cypher File",
                                        data=cypher_statements,
                                        file_name="knowledge_graph.cypher",
                                        mime="text/plain",
                                        key="save_cypher"
                                    )
                        
                        with button_col3:
                            if st.button("🔨 Build in Neo4j", key="build_neo4j", help="Build graph in Neo4j database"):
                                if st.session_state.get("enable_kg_construction", False):
                                    with st.spinner("Building knowledge graph in Neo4j..."):
                                        build_status = build_knowledge_graph(
                                            result.get("result", ""),
                                            st.session_state.get("neo4j_url", ""),
                                            st.session_state.get("neo4j_username", ""),
                                            st.session_state.get("neo4j_password", "")
                                        )
                                    if build_status["success"]:
                                        st.success("✅ Knowledge graph built successfully in Neo4j!")
                                        st.info(f"📊 {build_status.get('stats', 'Graph built successfully')}")
                                    else:
                                        st.error(f"❌ Failed to build knowledge graph: {build_status.get('error', 'Unknown error')}")
                                else:
                                    st.warning("⚠️ Please enable 'Knowledge Graph Construction' in the configuration first.")
                        
                        with button_col4:
                            # 添加全屏查看选项
                            if 'fullscreen_graph' not in st.session_state:
                                st.session_state.fullscreen_graph = False
                            
                            if st.button("🔍 Full Screen", key="fullscreen_btn", help="View graph in full screen"):
                                st.session_state.fullscreen_graph = True
                                st.rerun()
                        
                        # 检查是否进入全屏模式
                        if st.session_state.fullscreen_graph:
                            # 全屏模式显示
                            st.markdown("### 🔍 Full Screen Knowledge Graph View")
                            
                            # 退出全屏按钮
                            if st.button("⬅️ Back to Tab View", key="exit_fullscreen"):
                                st.session_state.fullscreen_graph = False
                                st.rerun()
                            
                            # 全屏图谱显示
                            if html_content:
                                # 使用更大的高度和全宽度显示
                                components.html(html_content, height=700, scrolling=True)
                                
                                # 全屏模式下的详细统计信息
                                with st.expander("📊 Detailed Graph Statistics", expanded=False):
                                    col_stats1, col_stats2 = st.columns(2)
                                    with col_stats1:
                                        st.text_area(
                                            "Graph Statistics",
                                            value=viz_stats,
                                            height=100,
                                            disabled=True
                                        )
                                    with col_stats2:
                                        # 显示图谱的详细信息
                                        try:
                                            result_data = json.loads(result.get("result", "{}"))
                                            if isinstance(result_data, dict) and 'triple_list' in result_data:
                                                triple_count = len(result_data['triple_list'])
                                                st.metric("Total Triples", triple_count)
                                            else:
                                                st.metric("Total Triples", "N/A")
                                        except:
                                            st.metric("Total Triples", "N/A")
                            else:
                                st.error(f"❌ Failed to create visualization: {viz_stats}")
                        
                        else:
                            # 正常选项卡模式显示图谱
                            if html_content:
                                st.markdown("**Knowledge Graph Visualization:**")
                                components.html(html_content, height=500, scrolling=True)
                            else:
                                st.error(f"❌ Failed to create visualization: {viz_stats}")
                
                else:
                    # 非Triple任务只显示Schema和Results
                    col_schema, col_result = st.columns(2)
                    
                    with col_schema:
                        st.markdown("**🤔 Generated Schema**")
                        schema_content = result.get("schema", "")
                        st.code(schema_content, language="python", line_numbers=False)
                        
                        # 下载按钮
                        if schema_content:
                            st.download_button(
                                label="📥 Download Schema",
                                data=schema_content,
                                file_name="generated_schema.json",
                                mime="application/json",
                                key="download_schema"
                            )
                    
                    with col_result:
                        st.markdown("**😉 Final Answer**")
                        result_content = result.get("result", "")
                        st.code(result_content, language="json", line_numbers=False)
                        
                        # 下载按钮
                        if result_content:
                            st.download_button(
                                label="📥 Download Result",
                                data=result_content,
                                file_name="final_answer.json",
                                mime="application/json",
                                key="download_result"
                            )
            
            else:
                # 显示错误信息，与webui.py的error_output_gr一致
                st.text_area(
                    "😵‍💫 Ops, an Error Occurred",
                    value=result.get("error", "Unknown error"),
                    height=200,
                    disabled=True
                )
        
        else:
            st.info("👆 Configure your model and input text to start extraction.")

def create_knowledge_graph_visualization(result_str):
    """从OneKE Triple抽取结果创建知识图谱可视化"""
    try:
        if isinstance(result_str, str):
            result_data = json.loads(result_str)
        else:
            result_data = result_str
        
        # 创建pyvis网络图
        net = Network(
            height="600px", 
            width="100%", 
            directed=True,
            notebook=False, 
            bgcolor="#ffffff", 
            font_color="#000000",
            cdn_resources='remote'
        )
        
        # 存储节点和边的信息
        nodes = set()
        edges = []
        
        # 处理OneKE Triple任务的输出格式：{"triple_list": [...]}
        if isinstance(result_data, dict) and 'triple_list' in result_data:
            triple_list = result_data['triple_list']
            for item in triple_list:
                if isinstance(item, dict) and 'head' in item and 'relation' in item and 'tail' in item:
                    head = str(item['head'])
                    tail = str(item['tail'])
                    relation = str(item['relation'])
                    
                    # 获取类型信息
                    head_type = item.get('head_type', 'Entity')
                    tail_type = item.get('tail_type', 'Entity')
                    
                    nodes.add((head, head_type))
                    nodes.add((tail, tail_type))
                    edges.append((head, tail, relation))
        
        # 处理简单的三元组列表格式（向后兼容）
        elif isinstance(result_data, list):
            for item in result_data:
                if isinstance(item, dict) and 'head' in item and 'relation' in item and 'tail' in item:
                    head = str(item['head'])
                    tail = str(item['tail'])
                    relation = str(item['relation'])
                    
                    nodes.add((head, 'Entity'))
                    nodes.add((tail, 'Entity'))
                    edges.append((head, tail, relation))
        
        if not nodes:
            return None, "No valid triples found for visualization"
        
        # 定义节点类型颜色
        type_colors = {
            'Person': '#ff9999',
            'Place': '#99ff99', 
            'Event': '#9999ff',
            'Organization': '#ffff99',
            'Entity': '#cccccc',
            'Time': '#ff99ff',
            'Number': '#99ffff'
        }
        
        # 添加节点到网络图
        for node_name, node_type in nodes:
            color = type_colors.get(node_type, '#cccccc')
            net.add_node(
                node_name, 
                label=node_name, 
                title=f"Type: {node_type}",
                color=color,
                size=20
            )
        
        # 添加边到网络图
        for head, tail, relation in edges:
            net.add_edge(
                head, 
                tail, 
                label=relation,
                title=relation,
                color='#666666',
                width=2
            )
        
        # 配置图形布局
        net.set_options("""
        {
            "physics": {
                "forceAtlas2Based": {
                    "gravitationalConstant": -50,
                    "centralGravity": 0.01,
                    "springLength": 100,
                    "springConstant": 0.08
                },
                "minVelocity": 0.75,
                "solver": "forceAtlas2Based"
            },
            "nodes": {
                "font": {
                    "size": 12
                }
            },
            "edges": {
                "font": {
                    "size": 10,
                    "align": "middle"
                },
                "arrows": {
                    "to": {
                        "enabled": true,
                        "scaleFactor": 1
                    }
                }
            }
        }
        """)
        
        # 生成HTML
        html_content = net.generate_html()
        
        # 统计信息
        stats = f"Nodes: {len(nodes)}\nRelationships: {len(edges)}"
        
        return html_content, stats
        
    except Exception as e:
        return None, f"Error creating visualization: {str(e)}"

if __name__ == "__main__":
    main()
