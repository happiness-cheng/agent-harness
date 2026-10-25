"""v07最小验收入口，默认不读取凭据或调用模型。"""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'src'))
from experiments.memory_v07.trial import main

if __name__=='__main__':main()
