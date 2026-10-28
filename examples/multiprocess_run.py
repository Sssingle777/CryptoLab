"""Four spawned processes record 40 independent simulated experiments.

Pass only batch.id. If adding Gurobi, each worker creates its own model/environment.
"""

from concurrent.futures import ProcessPoolExecutor
from itertools import repeat
import json
from multiprocessing import get_context

from cryptolab import get_batch_summary, track_batch, track_run


def worker(rounds: int, batch_id: str) -> str:
    with track_run(batch_id=batch_id, params={"rounds": rounds}) as run:
        run.log(f"进程模拟 {rounds} 轮实验")
        run.log_metrics({"satisfiable": True, "weight": 2 * rounds})
        run.output_path("result.txt").write_text(f"rounds={rounds}\n", encoding="utf-8")
        return run.id


def main() -> None:
    with track_batch(
        project="CryptoLab 示例", experiment="进程调用示例",
        name="40 次进程模拟", common_params={"time_limit": 300},
    ) as batch:
        with ProcessPoolExecutor(max_workers=4, mp_context=get_context("spawn")) as pool:
            run_ids = list(pool.map(worker, range(8, 48), repeat(batch.id)))
    print("Run count:", len(run_ids), "Unique IDs:", len(set(run_ids)))
    print("Batch ID:", batch.id)
    print("Summary:", json.dumps(get_batch_summary(batch.id), ensure_ascii=False))


if __name__ == "__main__":
    main()
