"""Allow `python -m pmb.cli` for scheduler-generated commands."""
from pmb.cli.main import app

if __name__ == "__main__":
    app()
