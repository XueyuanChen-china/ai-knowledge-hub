#!/usr/bin/env python
"""显式初始化 langgraph-checkpoint-postgres 的第三方表。"""

from pathlib import Path
import sys

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.graph.checkpointer import setup_graph_checkpoint_schema


if __name__ == "__main__":
    setup_graph_checkpoint_schema()
    print("LangGraph checkpoint tables are ready")
