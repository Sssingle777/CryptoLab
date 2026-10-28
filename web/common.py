import json
from pathlib import Path
from tempfile import TemporaryDirectory

import streamlit as st

from cryptolab.database import encode_dict
from cryptolab.files import add_attachment
from cryptolab.presentation import local_time


STATUS = {"COMPLETED": "已完成", "FAILED": "失败", "INTERRUPTED": "已中断",
          "RUNNING": "运行中", "UNCONFIRMED": "状态待确认"}


def json_input(text):
    value = json.loads(text)
    encode_dict(value)
    return value


def number_input(text):
    return None if not text.strip() else float(text)


def select_record(label, records, key, *, optional=False):
    mapping = {record["id"]: record for record in records}
    options = ([None] if optional else []) + list(mapping)
    if not options:
        return None
    if st.session_state.get(key) not in options:
        st.session_state.pop(key, None)
    chosen = st.selectbox(label, options, key=key,
                          format_func=lambda value: "全部 / 不指定" if value is None else
                          f"{mapping[value].get('name') or value[:8]} · {value[:8]}")
    return mapping.get(chosen)


def table_rows(rows, zone):
    return [{"Run ID": row["id"], "项目": row["project_name"], "实验方案": row["experiment_name"],
             "批次": row["batch_name"] or "—", "轮数": str(row["params"].get("rounds", "")),
             "来源": "自动" if row["source"] == "auto" else "手动", "状态": STATUS[row["display_status"]],
             "求解状态": row["solver_status"] or "—", "开始时间（本地）": local_time(row["started_at"], zone),
             "耗时（秒）": row["duration_s"], "已删除": row["deleted_at"] is not None} for row in rows]


def open_detail(run_id):
    st.session_state["selected_run_id"] = run_id
    st.session_state["navigate"] = "实验详情"
    st.rerun()


def flash(message, kind="success"):
    st.session_state["flash"] = (kind, message)


def save_uploads(run_id, uploads, reason="手动上传附件"):
    for upload in uploads:
        if Path(upload.name).name != upload.name or upload.name in {"", ".", ".."}:
            raise ValueError("附件名称必须是普通文件名")
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / upload.name
            path.write_bytes(upload.getbuffer())
            add_attachment(run_id, path, reason=reason)
