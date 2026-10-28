import streamlit as st

from cryptolab import get_batch_summary
from cryptolab.records import list_batches, list_experiments, list_projects, list_runs, save_experiment, save_project
from web.common import flash, open_detail, select_record, table_rows


def render(zone):
    st.title("项目管理")
    st.caption("项目对应研究主题，实验方案对应方法或模型。重命名保留记录的关联关系。")
    with st.expander("新建项目", expanded=not list_projects()):
        with st.form("new_project"):
            name = st.text_input("项目名称", key="new_project_name")
            description = st.text_area("项目说明", key="new_project_description")
            if st.form_submit_button("创建项目"):
                save_project(name, description=description or None)
                flash("项目已创建")
                st.rerun()
    project = select_record("选择项目", list_projects(), "manage_project")
    if not project:
        return
    with st.expander("编辑项目"):
        with st.form(f"edit_project_{project['id']}"):
            name = st.text_input("项目名称", value=project["name"], key=f"project_name_{project['id']}")
            description = st.text_area("项目说明", value=project["description"] or "", key=f"project_description_{project['id']}")
            if st.form_submit_button("保存项目"):
                save_project(name, project_id=project["id"], description=description or None)
                flash("项目已更新")
                st.rerun()
    with st.expander("新建实验方案"):
        with st.form("new_experiment"):
            name = st.text_input("方案名称", key="new_experiment_name")
            method = st.text_input("方法", placeholder="MILP / SAT / Other", key="new_experiment_method")
            cipher = st.text_input("算法名称", placeholder="PRESENT / SKINNY / AES", key="new_experiment_cipher")
            description = st.text_area("方案说明", key="new_experiment_description")
            if st.form_submit_button("创建方案"):
                save_experiment(project["id"], name, method=method or None, cipher_name=cipher or None, description=description or None)
                flash("实验方案已创建")
                st.rerun()
    experiments = list_experiments(project["id"])
    if not experiments:
        st.info("该项目暂无实验方案。")
        return
    st.dataframe([{key: row[key] for key in ("name", "method", "cipher_name", "description")} for row in experiments], hide_index=True)
    experiment = select_record("选择实验方案", experiments, "manage_experiment")
    with st.expander("编辑实验方案"):
        with st.form(f"edit_experiment_{experiment['id']}"):
            name = st.text_input("方案名称", value=experiment["name"], key=f"experiment_name_{experiment['id']}")
            method = st.text_input("方法", value=experiment["method"] or "", key=f"experiment_method_{experiment['id']}")
            cipher = st.text_input("算法名称", value=experiment["cipher_name"] or "", key=f"experiment_cipher_{experiment['id']}")
            description = st.text_area("方案说明", value=experiment["description"] or "", key=f"experiment_description_{experiment['id']}")
            if st.form_submit_button("保存方案"):
                save_experiment(project["id"], name, experiment_id=experiment["id"], method=method or None,
                                cipher_name=cipher or None, description=description or None)
                flash("实验方案已更新")
                st.rerun()
    rows = list_runs(experiment_id=experiment["id"])
    st.metric("该方案的有效 Run 数量", len(rows))
    if rows:
        st.dataframe(table_rows(rows, zone), hide_index=True, width="stretch")
        selected = st.selectbox("查看该方案的运行", [row["id"] for row in rows], key="project_run")
        if st.button("打开实验详情", key="project_open_run"):
            open_detail(selected)
    summaries = [get_batch_summary(batch["id"]) | {"name": batch["name"]} for batch in list_batches(experiment["id"])]
    if summaries:
        st.subheader("批次汇总")
        st.dataframe(summaries, hide_index=True, width="stretch")
