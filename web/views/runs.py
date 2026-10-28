from datetime import datetime
from zoneinfo import ZoneInfo

import streamlit as st

from cryptolab import get_batch_summary
from cryptolab.presentation import date_bounds, export_runs
from cryptolab.records import list_batches, list_experiments, list_projects, list_runs
from web.common import STATUS, open_detail, select_record, table_rows


def render(zone):
    st.title("实验列表")
    st.caption("自动记录与手动记录统一查看；待确认状态不会自动改写实验结果。")
    first, second, third = st.columns(3)
    with first:
        project = select_record("项目", list_projects(), "filter_project", optional=True)
    with second:
        experiments = list_experiments(project["id"] if project else None)
        experiment = select_record("实验方案", experiments, "filter_experiment", optional=True)
    with third:
        batches = list_batches(experiment["id"] if experiment else None)
        if project:
            ids = {item["id"] for item in experiments}
            batches = [item for item in batches if item["experiment_id"] in ids]
        batch = select_record("批次", batches, "filter_batch", optional=True)
    left, middle, right = st.columns(3)
    with left:
        rounds = st.text_input("轮数（留空表示不限）", key="filter_rounds")
        status = st.selectbox("运行状态", [None, *STATUS], key="filter_status",
                              format_func=lambda value: "全部" if value is None else STATUS[value])
    with middle:
        solver = st.text_input("求解状态（精确匹配）", placeholder="例如 OPTIMAL / TIME_LIMIT", key="filter_solver")
        source = st.selectbox("记录来源", [None, "auto", "manual"], key="filter_source",
                              format_func=lambda value: {None: "全部", "auto": "自动", "manual": "手动"}[value])
    with right:
        cipher = st.text_input("算法名称（精确匹配）", key="filter_cipher")
        deleted = st.selectbox("记录范围", ["exclude", "only", "include"], key="filter_deleted",
                               format_func=lambda value: {"exclude": "正常记录", "only": "已删除记录", "include": "全部记录"}[value])
    keyword = st.text_input("关键词", placeholder="项目、方案、Run ID、参数或备注", key="filter_keyword")
    start = end = None
    if st.checkbox("按实验开始日期筛选", key="filter_dates"):
        a, b = st.columns(2)
        today = datetime.now(ZoneInfo(zone)).date()
        start_date = a.date_input("开始日期", value=today, key="filter_date_start")
        end_date = b.date_input("结束日期（包含当天）", value=today, key="filter_date_end")
        start, end = date_bounds(start_date, end_date, zone)
        st.caption("日期按显示时区解释；开始时间未知的记录不匹配日期筛选。")
    refresh = st.checkbox("每 5 秒刷新列表", key="auto_refresh")
    st.button("立即刷新", key="refresh_runs")
    filters = dict(project_id=project["id"] if project else None,
                   experiment_id=experiment["id"] if experiment else None,
                   batch_id=batch["id"] if batch else None,
                   rounds=float(rounds) if rounds.strip() else None,
                   status=status, solver_status=solver.strip() or None, cipher_name=cipher.strip() or None,
                   source=source, keyword=keyword, deleted=deleted, started_from=start, started_before=end)

    @st.fragment(run_every=5 if refresh else None)
    def results():
        rows = list_runs(**filters)
        columns = st.columns(4)
        columns[0].metric("匹配记录", len(rows))
        for column, label, state in zip(columns[1:], ("已完成", "运行中", "待确认"), ("COMPLETED", "RUNNING", "UNCONFIRMED")):
            column.metric(label, sum(row["display_status"] == state for row in rows))
        if batch:
            st.caption("所选批次汇总（不受列表的其他筛选条件影响，排除软删除记录）")
            st.json(get_batch_summary(batch["id"]), expanded=False)
        if not rows:
            st.info("暂无匹配记录。可以运行 SDK 示例，或在“手动录入”中添加历史实验。")
            return
        st.dataframe(table_rows(rows, zone), hide_index=True, width="stretch")
        a, b = st.columns(2)
        a.download_button("导出当前筛选 CSV", export_runs(rows, "csv"), "cryptolab-runs.csv", "text/csv", key="export_csv")
        b.download_button("导出当前筛选 JSON", export_runs(rows, "json"), "cryptolab-runs.json", "application/json", key="export_json")
        mapping = {row["id"]: row for row in rows}
        selected = st.selectbox("选择记录查看详情", list(mapping), key="list_selected_run",
                                format_func=lambda value: f"{value[:8]} · {mapping[value]['project_name']} / {mapping[value]['experiment_name']}")
        if st.button("查看详情", key="open_detail"):
            open_detail(selected)
    results()
