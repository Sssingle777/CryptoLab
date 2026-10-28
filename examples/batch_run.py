"""Sequential batch: one Batch, five Runs; no parallel reliability assumptions."""

from cryptolab import track_batch, track_run


def main() -> None:
    with track_batch(
        project="CryptoLab 示例", experiment="批量模拟实验", name="8–12 轮测试",
        common_params={"time_limit": 300, "method": "SAT-like"},
    ) as batch:
        for rounds in range(8, 13):
            with track_run(batch_id=batch.id, params={"rounds": rounds}) as run:
                run.log(f"正在模拟 {rounds} 轮实验")
                run.log_metrics({"satisfiable": True, "weight": 2 * rounds})
                run.output_path("result.txt").write_text(
                    f"rounds={rounds}, weight={2 * rounds}\n", encoding="utf-8",
                )
                print("Run ID:", run.id)
    print("Batch ID:", batch.id)


if __name__ == "__main__":
    main()
