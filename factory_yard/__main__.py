"""Run from projekt: python -m factory_yard."""
import argparse
import asyncio
import json
from pathlib import Path


def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="Gyári készjármű-parkolási szimulátor, valódi MQTT-adatúttal")
    parser.add_argument("role", nargs="?", choices=["run", "broker", "simulator", "coordinator"], default="run")
    parser.add_argument("--config", type=Path, default=root / "config" / "default.json")
    parser.add_argument("--run-id", default="standalone")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--strategy", choices=["nearest", "fixed"], default="nearest")
    parser.add_argument("--speed", type=float, choices=[1, 2, 4, 8], default=1)
    parser.add_argument("--http-port", type=int, default=8765)
    parser.add_argument("--external-broker", action="store_true")
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding="utf-8"))
    if args.role == "broker":
        from .broker import serve
        asyncio.run(serve(config["mqtt"]["host"], config["mqtt"]["port"]))
    elif args.role in ("simulator", "coordinator"):
        from .runtime import simulator, coordinator
        if args.role == "simulator":
            simulator(config, args.run_id, args.seed, args.speed)
        else:
            coordinator(config, args.run_id, args.strategy)
    else:
        from .server import Application, serve
        # The display defaults to the source-backed larger yard. Direct model
        # experiments retain the original baseline fixture in default.json.
        if args.config.resolve() == (root / "config" / "default.json").resolve():
            from .scenario import compile_scenario
            _, config = compile_scenario(config)
        app = Application(config, args.config.resolve(), args.seed, args.strategy, args.speed, args.external_broker)
        try:
            app.start()
            serve(app, args.http_port)
        finally:
            app.close()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
