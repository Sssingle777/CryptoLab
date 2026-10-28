import streamlit as st

from cryptolab import create_manual_run
from cryptolab.presentation import input_time
from cryptolab.records import list_batches, list_experiments, list_projects
from web.common import STATUS, flash, json_input, number_input, open_detail, save_uploads, select_record


def render(zone):
    st.title("手动录入")
    st.caption("补录已执行的历史实验。未知时间留空，不把录入时间当成实验时间。")
    project = select_record("项目", list_projects(), "manual_project")
    if not project:
        st.info("请先到“项目管理”创建项目和实验方案。")
        return
    experiment = select_record("实验方案", list_experiments(project["id"]), "manual_experiment")
    if not experiment:
        st.info("请先为该项目创建实验方案。")
        return
    batch = select_record("批次（可选）", list_batches(experiment["id"]), "manual_batch", optional=True)
    with st.form("manual_run"):
        a, b = st.columns(2)
        params = a.text_area("参数 JSON", value="{}", height=160, key="manual_params")
        metrics = b.text_area("结果 JSON", value="{}", height=160, key="manual_metrics")
        status = st.selectbox("运行状态", ["COMPLETED", "FAILED", "INTERRUPTED"], format_func=STATUS.get, key="manual_status")
        a, b = st.columns(2)
        start = a.text_input("开始时间（本地，可留空）", placeholder="2026-10-28 14:30:00", key="manual_start")
        end = b.text_input("结束时间（本地，可留空）", key="manual_end")
        duration = st.text_input("实际耗时（秒，可留空）", key="manual_duration")
        st.caption("两端时间均已知且未填耗时时，按时间差计算；只知道日期可写在备注中。")
        notes = st.text_area("备注", key="manual_notes")
        a, b, c = st.columns(3)
        solver_name = a.text_input("求解器", key="manual_solver_name")
        solver_status = b.text_input("求解状态", key="manual_solver_status")
        solver_runtime = c.text_input("求解耗时（秒）", key="manual_solver_runtime")
        uploads = st.file_uploader("附件（可选，不执行文件内容）", accept_multiple_files=True, key="manual_uploads")
        reason = st.text_input("录入说明", value="手动录入历史实验", key="manual_reason")
        if st.form_submit_button("保存历史实验", type="primary"):
            run_id = create_manual_run(
                project=project["name"], experiment=experiment["name"], batch_id=batch["id"] if batch else None,
                params=json_input(params), metrics=json_input(metrics), status=status,
                started_at=input_time(start, zone), finished_at=input_time(end, zone),
                duration_s=number_input(duration), notes=notes or None, solver_name=solver_name or None,
                solver_status=solver_status or None, solver_runtime_s=number_input(solver_runtime), reason=reason,
            )
            try:
                save_uploads(run_id, uploads)
            except Exception as error:
                flash(f"记录已创建，但附件未全部保存：{error}。可在详情页继续添加或补录。", "error")
            else:
                flash("历史实验已保存")
            open_detail(run_id)
