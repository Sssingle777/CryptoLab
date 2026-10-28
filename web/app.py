"""Start from the repository: python -m streamlit run web/app.py."""

import sys
from pathlib import Path
from zoneinfo import ZoneInfo

import streamlit as st

# Streamlit adds web/ to sys.path; make sibling view modules importable from
# any working directory without moving UI code into the core package.
repository = str(Path(__file__).resolve().parent.parent)
if repository not in sys.path:
    sys.path.insert(0, repository)

from cryptolab.database import data_home
from web.views import details, manual, projects, runs


st.set_page_config(page_title="CryptoLab", page_icon="🔬", layout="wide")
if st.get_option("server.address") not in {"127.0.0.1", "localhost", "::1"}:
    st.error("CryptoLab 仅支持本机访问。请加上 --server.address 127.0.0.1 启动。")
    st.stop()

st.sidebar.title("CryptoLab")
st.sidebar.caption("本地实验记录 · 0.3.0")
if "navigate" in st.session_state:
    st.session_state["page"] = st.session_state.pop("navigate")
page = st.sidebar.radio("页面", ["实验列表", "项目管理", "实验详情", "手动录入"], key="page")
zone = st.sidebar.text_input("显示时区", value="Asia/Shanghai", key="timezone")
st.sidebar.caption("时间以此时区显示，数据库保留 UTC。")
st.sidebar.caption(f"数据目录：{data_home()}")
try:
    ZoneInfo(zone)
except (ValueError, KeyError):
    st.error("请输入有效的 IANA 时区，例如 Asia/Shanghai 或 Europe/London。")
    st.stop()

if "flash" in st.session_state:
    kind, message = st.session_state.pop("flash")
    getattr(st, kind)(message)
try:
    {"实验列表": runs.render, "项目管理": projects.render,
     "实验详情": details.render, "手动录入": manual.render}[page](zone)
except Exception as error:
    st.error(f"操作未完成：{error}")
