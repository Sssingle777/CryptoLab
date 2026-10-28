from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import subprocess
import sys
import threading
from types import SimpleNamespace

import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest

from cryptolab import create_manual_run, track_run, update_run
from cryptolab.records import get_run, list_experiments, list_projects, list_runs, save_experiment, save_project
from tests.test_record_management import stale_row
from web.common import save_uploads


APP = Path(__file__).resolve().parents[1] / "web" / "app.py"


def button(app, label):
    return next(item for item in app.button if item.label == label)


def assert_clean(app):
    assert not app.exception, [item.value for item in app.exception]
    assert not app.error, [item.value for item in app.error]


def app_on(page, run_id=None):
    app = AppTest.from_file(str(APP), default_timeout=20)
    app.session_state["page"] = page
    if run_id:
        app.session_state["selected_run_id"] = run_id
    app.run()
    assert_clean(app)
    return app


@pytest.mark.parametrize("page", ["实验列表", "项目管理", "实验详情", "手动录入"])
def test_empty_pages_render_without_exceptions(page):
    app_on(page)


def test_project_and_experiment_forms():
    app = app_on("项目管理")
    app.text_input(key="new_project_name").set_value("科研项目")
    app.text_area(key="new_project_description").set_value("测试说明")
    button(app, "创建项目").click().run()
    assert_clean(app)
    assert list_projects()[0]["name"] == "科研项目"
    app.text_input(key="new_experiment_name").set_value("差分搜索")
    app.text_input(key="new_experiment_method").set_value("MILP")
    app.text_input(key="new_experiment_cipher").set_value("PRESENT")
    button(app, "创建方案").click().run()
    assert_clean(app)
    experiment = list_experiments()[0]
    assert experiment["method"] == "MILP" and experiment["cipher_name"] == "PRESENT"
    app.text_input(key=f"experiment_name_{experiment['id']}").set_value("重命名方案")
    button(app, "保存方案").click().run()
    assert_clean(app)
    assert list_experiments()[0]["name"] == "重命名方案"


def test_manual_form_validation_and_save_navigates_to_detail():
    project = save_project("p")
    save_experiment(project, "e")
    app = app_on("手动录入")
    app.text_area(key="manual_params").set_value("not json")
    button(app, "保存历史实验").click().run()
    assert app.error and not list_runs()
    app.text_area(key="manual_params").set_value('{"rounds": 8}')
    app.text_area(key="manual_metrics").set_value('{"weight": 16, "sat": true}')
    app.text_input(key="manual_start").set_value("2025-01-01 08:00:00")
    button(app, "保存历史实验").click().run()
    assert_clean(app)
    row = list_runs()[0]
    assert row["started_at"] == "2025-01-01T00:00:00.000000+00:00"
    assert row["finished_at"] is None and row["duration_s"] is None
    assert row["metrics"]["sat"] is True and row["source"] == "manual"
    assert app.sidebar.radio(key="page").value == "实验详情"
    assert [tab.label for tab in app.tabs] == ["参数与复现", "结果", "日志", "附件", "修改历史"]


def test_list_filters_and_exports_visible():
    first = create_manual_run(project="p", experiment="e", params={"rounds": 8}, solver_status="TIME_LIMIT")
    create_manual_run(project="p", experiment="e", params={"rounds": 9}, status="FAILED")
    app = app_on("实验列表")
    assert len(app.dataframe[0].value) == 2
    app.text_input(key="filter_rounds").set_value("8").run()
    assert_clean(app)
    assert app.dataframe[0].value["Run ID"].tolist() == [first]
    assert len(app.get("download_button")) == 2
    button(app, "查看详情").click().run()
    assert_clean(app)
    assert app.text_input(key="selected_run_id").value == first


def test_editor_rejects_stale_version_and_preserves_raw_snapshot():
    with track_run(project="p", experiment="e") as run:
        run.log_metrics({"value": 0})
    app = app_on("实验详情", run.id)
    prefix = f"edit_{run.id}_"
    update_run(run.id, {"metrics": {"value": 1}}, reason="another editor")
    app.text_area(key=prefix + "metrics").set_value('{"value": 2}')
    app.text_input(key=prefix + "reason").set_value("stale edit")
    button(app, "保存修改").click().run()
    assert any("Record changed" in item.value for item in app.error)
    assert get_run(run.id)["metrics"] == {"value": 1}
    button(app, "重新载入（放弃未保存编辑）").click().run()
    assert_clean(app)
    app.text_area(key=prefix + "metrics").set_value('{"value": 3}')
    app.text_input(key=prefix + "reason").set_value("reviewed")
    button(app, "保存修改").click().run()
    assert_clean(app)
    row = get_run(run.id)
    assert row["metrics"] == {"value": 3}
    assert json.loads(row["raw_metrics_json"]) == {"value": 0}


def test_detail_soft_delete_restore_and_confirmation_forms():
    run_id = create_manual_run(project="p", experiment="e")
    app = app_on("实验详情", run_id)
    app.checkbox(key=f"delete_checked_{run_id}").check()
    app.text_input(key=f"delete_reason_{run_id}").set_value("mistake")
    button(app, "软删除记录").click().run()
    assert_clean(app)
    assert get_run(run_id)["deleted_at"]
    app.text_input(key=f"delete_reason_{run_id}").set_value("restore")
    button(app, "恢复记录").click().run()
    assert_clean(app)
    assert get_run(run_id)["deleted_at"] is None
    stale_row(run_id)
    button(app, "重新载入（放弃未保存编辑）").click().run()
    prefix = f"confirm_{run_id}_"
    app.checkbox(key=prefix + "checked").check()
    app.text_input(key=prefix + "reason").set_value("verified archived experiment stopped")
    button(app, "确认实验已停止").click().run()
    assert_clean(app)
    assert get_run(run_id)["status"] == "INTERRUPTED"
    assert get_run(run_id)["finished_at"] is None


def test_readonly_scan_and_explicit_file_registration_in_ui(home):
    run_id = create_manual_run(project="p", experiment="e")
    (home / "runs" / run_id / "result.txt").write_text("unregistered result")
    app = app_on("实验详情", run_id)
    button(app, "只读扫描").click().run()
    assert_clean(app)
    assert not get_run(run_id)["artifacts"]
    app.checkbox(key=f"register_checked_{run_id}").check()
    app.text_input(key=f"register_reason_{run_id}").set_value("recovered output")
    button(app, "补录所选文件").click().run()
    assert_clean(app)
    assert len(get_run(run_id)["artifacts"]) == 1
    button(app, "准备附件下载").click().run()
    assert_clean(app)
    assert any(item.label == "下载附件" for item in app.get("download_button"))


def test_upload_helper_accepts_historical_logs_but_never_overwrites():
    run_id = create_manual_run(project="p", experiment="e")
    upload = SimpleNamespace(name="stdout.log", getbuffer=lambda: memoryview(b"historic log\n"))
    save_uploads(run_id, [upload])
    assert get_run(run_id)["artifacts"][0]["original_name"] == "stdout.log"
    with pytest.raises(FileExistsError):
        save_uploads(run_id, [upload])


def test_ui_queries_while_worker_threads_are_running():
    entered = threading.Barrier(5)
    release = threading.Event()
    def worker(index):
        with track_run(project="p", experiment="concurrent UI", params={"rounds": index}) as run:
            run.log_metrics({"value": index})
            entered.wait(timeout=10)
            assert release.wait(20)
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(worker, index) for index in range(4)]
        try:
            entered.wait(timeout=10)
            app = app_on("实验列表")
            assert app.dataframe[0].value["状态"].tolist() == ["运行中"] * 4
        finally:
            release.set()
        for future in futures:
            future.result(timeout=20)
    app.run()
    assert_clean(app)
    assert app.dataframe[0].value["状态"].tolist() == ["已完成"] * 4


def test_core_sdk_never_imports_streamlit_or_web_dependencies():
    code = '''
import sys
class BlockWeb:
    def find_spec(self, fullname, *args):
        if fullname.split('.')[0] in {'streamlit', 'pandas', 'numpy'}:
            raise AssertionError('core imported optional web dependency')
sys.meta_path.insert(0, BlockWeb())
from cryptolab import track_run, create_manual_run
with track_run(project='core', experiment='plain') as run:
    run.log_metrics({'ok': True})
create_manual_run(project='core', experiment='history')
'''
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
