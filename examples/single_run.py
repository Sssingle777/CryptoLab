"""Run with: python examples/single_run.py (no solver required)."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory

from cryptolab import track_run


def main() -> None:
    with TemporaryDirectory() as temporary:
        source = Path(temporary) / "input.txt"
        source.write_text("模拟实验输入\n", encoding="utf-8")
        with track_run(
            project="CryptoLab 示例", experiment="单次模拟实验",
            params={"rounds": 12, "time_limit": 300}, capture_console=True,
        ) as run:
            print("开始模拟实验")
            run.log_params({"seed": 42})
            result = {"satisfiable": True, "weight": 24, "trail": [1, 2, 4]}
            run.log_metrics(result)
            run.log("模拟实验完成")
            run.output_path("result.json").write_text(
                json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8",
            )
            run.add_file(source)
        print("Run ID:", run.id)


if __name__ == "__main__":
    main()
