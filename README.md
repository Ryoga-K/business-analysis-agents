# business-analysis-agents

OpenAI Agents SDKを用いて、業務文書から業務知識を抽出し、Workflow RDF、Data RDF、Rule RDFを生成・検証する研究用プロトタイプです。

現時点では最小の起動確認とテストだけを実装しています。エージェント実行、RDF変換、SHACL検証、修正ループ、人間レビューは読みやすいひな型として配置し、未実装箇所にはTODOを残しています。

## 方針

- エージェントごとの入力と出力はPydanticモデルで明示する。
- LLMの出力から直接Turtle文字列を作らず、構造化データをPythonコードでRDFへ変換する。
- SHACLの適合判定はLLMではなくpySHACLで行う。
- 反復回数と終了条件はPythonコードで管理する。
- 実行結果は `outputs/run_YYYYMMDD_HHMMSS/` 以下に保存する。
- SQLやデータベース、Web UIは使用しない。
- 人間レビューは `input()` と `print()` によるCLIで行う。

## ディレクトリ構成

```text
.
├── main.py
├── pyproject.toml
├── .env.example
├── .gitignore
├── README.md
├── src/
│   └── business_analysis_agents/
│       ├── agents/
│       ├── rdf/
│       ├── review/
│       ├── shacl/
│       ├── config.py
│       ├── controller.py
│       ├── document_loader.py
│       ├── models.py
│       └── repair_loop.py
└── tests/
    └── test_startup.py
```

## セットアップ

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
Copy-Item .env.example .env
```

`.env` に `OPENAI_API_KEY` を設定してください。現在の起動確認ではAPI呼び出しは行いません。

## 起動

```powershell
python main.py
```

期待される出力:

```text
システムを開始しました
```

## テスト

```powershell
pytest
```

## 今後の実装候補

- PyMuPDFによるPDF文書読み込み
- OpenAI Agents SDKによる各抽出エージェントの実行
- Pydantic構造化出力からRDFLibグラフへの変換
- pySHACLによる構造検証とRDF間整合性検証
- 違反箇所に応じた抽出エージェントの再実行
- 自動修正不能時のCLI人間レビュー
