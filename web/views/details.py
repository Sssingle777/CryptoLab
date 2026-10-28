import json

import streamlit as st

from cryptolab import confirm_run_stopped, get_batch_summary, restore_run, soft_delete_run, update_run
from cryptolab.files import (available_logs, log_tail, open_attachment, open_log,
                             register_existing_file, scan_unregistered_files, search_log)
from cryptolab.presentation import input_time, local_time
from cryptolab.records import get_run
from web.common import STATUS, flash, json_input, number_input, save_uploads


def _reset_editor(run_id):
    for key in list(st.session_state):
        if key.startswith((f"edit_{run_id}_", f"confirm_{run_id}_")):
            del st.session_state[key]


def _edit(row, zone):
    run_id = row["id"]
    prefix = f"edit_{run_id}_"
    version = st.session_state.setdefault(prefix + "version", row["updated_at"])
    with st.expander("编辑参数、结果与时间"):
        if row["status"] == "RUNNING" or row["deleted_at"]:
            st.info("运行中或已删除记录不可编辑。待确认记录须先完成安全确认；已删除记录须先恢复。")
            return
        with st.form(prefix + "form"):
            a, b = st.columns(2)
            params = a.text_area("参数 JSON", json.dumps(row["params"], ensure_ascii=False, indent=2), height=180, key=prefix + "params")
            metrics = b.text_area("结果 JSON", json.dumps(row["metrics"], ensure_ascii=False, indent=2), height=180, key=prefix + "metrics")
            a, b = st.columns(2)
            start = a.text_input("开始时间（本地）", local_time(row["started_at"], zone), key=prefix + "start")
            end = b.text_input("结束时间（本地）", local_time(row["finished_at"], zone), key=prefix + "end")
            duration = st.text_input("总耗时（秒，留空表示未知）", "" if row["duration_s"] is None else str(row["duration_s"]), key=prefix + "duration")
            notes = st.text_area("备注", row["notes"] or "", key=prefix + "notes")
            a, b, c = st.columns(3)
            solver_name = a.text_input("求解器", row["solver_name"] or "", key=prefix + "solver_name")
            solver_status = b.text_input("求解状态", row["solver_status"] or "", key=prefix + "solver_status")
            solver_version = c.text_input("求解器版本", row["solver_version"] or "", key=prefix + "solver_version")
            runtime = st.text_input("求解耗时（秒）", "" if row["solver_runtime_s"] is None else str(row["solver_runtime_s"]), key=prefix + "runtime")
            reason = st.text_input("修改原因（必填）", key=prefix + "reason")
            if st.form_submit_button("保存修改", type="primary"):
                update_run(run_id, {"params": json_input(params), "metrics": json_input(metrics),
                                    "started_at": input_time(start, zone), "finished_at": input_time(end, zone),
                                    "duration_s": number_input(duration), "notes": notes or None,
                                    "solver_name": solver_name or None, "solver_status": solver_status or None,
                                    "solver_version": solver_version or None, "solver_runtime_s": number_input(runtime)},
                           reason=reason, expected_updated_at=version)
                _reset_editor(run_id)
                flash("修改已保存并写入审计，原始快照未改变")
                st.rerun()


def _lifecycle(row, zone):
    run_id = row["id"]
    if row["display_status"] == "UNCONFIRMED" and row["deleted_at"] is None:
        prefix = f"confirm_{run_id}_"
        witness = st.session_state.setdefault(prefix + "witness", {
            "updated_at": row["updated_at"], "last_heartbeat_at": row["last_heartbeat_at"],
        })
        with st.expander("人工确认异常结束"):
            st.warning("心跳超时不等于进程死亡。请先自行核实实验已停止；此操作不会停止或调度进程。")
            st.write({"主机": row["hostname"], "进程 ID": row["process_id"], "最后心跳（UTC）": row["last_heartbeat_at"]})
            with st.form(prefix + "form"):
                confirmed = st.checkbox("我已核实实验进程已停止，不会继续写入", key=prefix + "checked")
                status = st.selectbox("确认后的状态", ["INTERRUPTED", "FAILED"], format_func=STATUS.get, key=prefix + "status")
                end = st.text_input("实际结束时间（本地；未知则留空）", key=prefix + "end")
                reason = st.text_input("确认依据与原因（必填）", key=prefix + "reason")
                if st.form_submit_button("确认实验已停止"):
                    confirm_run_stopped(run_id, expected_heartbeat_at=witness["last_heartbeat_at"],
                                        expected_updated_at=witness["updated_at"], confirmed_stopped=confirmed,
                                        status=status, finished_at=input_time(end, zone), reason=reason)
                    _reset_editor(run_id)
                    flash("已记录人工确认；未提供的结束时间和耗时保持未知")
                    st.rerun()
    with st.expander("删除与恢复"):
        if row["status"] == "RUNNING":
            st.info("运行中记录不能删除；待确认记录需先安全确认结束。")
            return
        with st.form(f"delete_{run_id}"):
            reason = st.text_input("操作原因（必填）", key=f"delete_reason_{run_id}")
            confirmed = st.checkbox("确认此操作；附件保留在磁盘中", key=f"delete_checked_{run_id}")
            restoring = row["deleted_at"] is not None
            if st.form_submit_button("恢复记录" if restoring else "软删除记录"):
                if not confirmed:
                    raise ValueError("请勾选操作确认")
                (restore_run if restoring else soft_delete_run)(run_id, reason=reason, expected_updated_at=row["updated_at"])
                _reset_editor(run_id)
                flash("记录已恢复" if restoring else "记录已软删除，附件未删除")
                st.rerun()


def _logs(run_id):
    logs = available_logs(run_id)
    if not logs:
        st.info("暂无日志。")
        return
    name = st.selectbox("日志文件", logs, key=f"log_name_{run_id}")
    st.caption("默认显示末尾最多 256 KiB / 300 行。文本按原样展示，不执行内容。")
    st.code(log_tail(run_id, name), language=None)
    term = st.text_input("日志搜索（区分大小写）", key=f"log_search_{run_id}")
    if st.button("搜索完整日志", key=f"log_search_button_{run_id}"):
        result = search_log(run_id, name, term)
        st.code("\n".join(result["matches"]) or "无匹配", language=None)
        if result["limited"]:
            st.caption("已达到 200 条结果上限；可下载日志继续查找。")
    if st.button("准备完整日志下载", key=f"log_prepare_{run_id}"):
        st.session_state[f"prepared_log_{run_id}"] = name
    if st.session_state.get(f"prepared_log_{run_id}") == name:
        with open_log(run_id, name) as file:
            st.download_button("下载日志", file, file_name=name, mime="application/octet-stream", key=f"log_download_{run_id}")


def _attachments(row):
    run_id = row["id"]
    if row["artifacts"]:
        st.dataframe([{key: item[key] for key in ("id", "kind", "original_name", "relative_path", "size_bytes", "sha256")}
                      for item in row["artifacts"]], hide_index=True, width="stretch")
        mapping = {item["id"]: item for item in row["artifacts"]}
        chosen = st.selectbox("选择附件", list(mapping), format_func=lambda value: mapping[value]["relative_path"], key=f"attachment_{run_id}")
        if st.button("准备附件下载", key=f"attachment_prepare_{run_id}"):
            st.session_state[f"prepared_attachment_{run_id}"] = chosen
        if st.session_state.get(f"prepared_attachment_{run_id}") == chosen:
            with open_attachment(chosen) as file:
                st.download_button("下载附件", file, file_name=mapping[chosen]["original_name"],
                                   mime="application/octet-stream", key=f"attachment_download_{run_id}")
    else:
        st.info("暂无已登记附件。")
    writable = row["status"] != "RUNNING" and row["deleted_at"] is None
    with st.expander("上传附件"):
        with st.form(f"upload_{run_id}"):
            uploads = st.file_uploader("选择已有结果或日志文件", accept_multiple_files=True, key=f"uploads_{run_id}")
            reason = st.text_input("附件说明", value="手动上传附件", key=f"upload_reason_{run_id}")
            if st.form_submit_button("添加附件", disabled=not writable):
                if not uploads:
                    raise ValueError("请选择附件")
                save_uploads(run_id, uploads, reason)
                flash("附件已复制并登记")
                st.rerun()
    with st.expander("检测未登记文件"):
        st.caption("扫描只读取本 Run 目录，不写入数据库。运行中可检测，结束或人工确认后才能补录。")
        if st.button("只读扫描", key=f"scan_{run_id}"):
            with st.spinner("检查文件与散列…"):
                st.session_state[f"scan_result_{run_id}"] = scan_unregistered_files(run_id)
        scan = st.session_state.get(f"scan_result_{run_id}")
        if scan is None:
            return
        if scan["rejected"]:
            st.warning("以下路径未被接受：")
            st.dataframe(scan["rejected"], hide_index=True)
        if not scan["files"]:
            st.info("没有可补录的未登记文件。")
            return
        st.dataframe([{key: item[key] for key in ("name", "size_bytes", "sha256")} for item in scan["files"]], hide_index=True)
        mapping = {item["name"]: item for item in scan["files"]}
        with st.form(f"register_{run_id}"):
            name = st.selectbox("选择补录文件", list(mapping), key=f"register_name_{run_id}")
            checked = st.checkbox("我已核对文件属于本次实验且已写入完成", key=f"register_checked_{run_id}")
            reason = st.text_input("补录原因", key=f"register_reason_{run_id}")
            if st.form_submit_button("补录所选文件", disabled=not writable):
                if not checked:
                    raise ValueError("请先核对文件并勾选确认")
                candidate = mapping[name]
                register_existing_file(run_id, name, expected_sha256=candidate["sha256"],
                                       expected_size=candidate["size_bytes"], reason=reason)
                st.session_state.pop(f"scan_result_{run_id}", None)
                flash("文件已安全补录并记录审计")
                st.rerun()


def render(zone):
    st.title("实验详情")
    run_id = st.text_input("Run ID", key="selected_run_id")
    if not run_id:
        st.info("从实验列表选择记录，或输入完整 Run ID。")
        return
    if st.button("重新载入（放弃未保存编辑）", key="reload_detail"):
        _reset_editor(run_id)
        st.rerun()
    row = get_run(run_id)
    st.subheader(f"{row['project_name']} / {row['experiment_name']}")
    if row["deleted_at"]:
        st.warning("这是一条已软删除记录。数据与附件保留，可在下方恢复。")
    a, b, c, d = st.columns(4)
    a.metric("运行状态", STATUS[row["display_status"]])
    b.metric("来源", "自动" if row["source"] == "auto" else "手动")
    c.metric("总耗时（秒）", "未知" if row["duration_s"] is None else f"{row['duration_s']:.6g}")
    d.metric("求解状态", row["solver_status"] or "—")
    times = ("started_at", "finished_at", "created_at", "updated_at", "last_heartbeat_at", "deleted_at")
    st.dataframe([{"字段": field, "本地时间": local_time(row[field], zone), "UTC 原值": row[field] or "未知"}
                  for field in times], hide_index=True, width="stretch")
    parameters, results, logs, attachments, history = st.tabs(["参数与复现", "结果", "日志", "附件", "修改历史"])
    with parameters:
        st.json(row["params"])
        if row["notes"]:
            st.text(row["notes"])
        with st.expander("复现信息与原始参数快照"):
            st.json({key: row[key] for key in ("hostname", "process_id", "thread_id", "script_path", "working_directory", "git_commit", "command_json")})
            st.json(json.loads(row["raw_params_json"]) if row["raw_params_json"] else None)
        if row["batch_id"]:
            st.caption(f"所属批次：{row['batch_name'] or row['batch_id']}")
            st.json(get_batch_summary(row["batch_id"]), expanded=False)
    with results:
        st.json(row["metrics"])
        st.write({key: row[key] for key in ("solver_name", "solver_version", "solver_status", "solver_runtime_s", "error_type", "error_message")})
        with st.expander("原始结果快照（只读）"):
            st.json(json.loads(row["raw_metrics_json"]) if row["raw_metrics_json"] else None)
    with logs:
        try:
            _logs(run_id)
        except (OSError, ValueError) as error:
            st.warning(f"日志暂不可读取：{error}")
    with attachments:
        _attachments(row)
    with history:
        if not row["changes"]:
            st.info("暂无人工修改。")
        for change in reversed(row["changes"]):
            with st.expander(f"{local_time(change['changed_at'], zone)} · {change['field_name']}"):
                st.text(f"原因：{change['reason']}\n操作人：{change['actor']}\nUTC：{change['changed_at']}")
                st.json({"old": json.loads(change["old_value_json"]), "new": json.loads(change["new_value_json"])})
    _edit(row, zone)
    _lifecycle(row, zone)
