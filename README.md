# business-analysis-agents

OpenAI Agents SDKを用いて、業務文書から業務知識を抽出し、Workflow RDF、Data RDF、Rule RDFを生成・検証する研究用プロトタイプです。

現在は、PDFから業務シナリオJSONを生成し、そのシナリオJSONを入力としてWorkflow RDF、Data RDF、Rule RDFを生成・検証・修正する最小パイプラインまで実装しています。Web UI、DB保存、人間レビューUI、RDF間Cross Reviewはまだ実装していません。

## 実装済み

- PyMuPDFによるPDFテキストのページ単位抽出
- Scenario Agentによる固定フォーマットの業務シナリオJSON生成
- Workflow AgentによるWorkflow RDF生成
- 固定Workflow ontology / SHACL Shapesを参照したWorkflow RDF生成
- Workflow RDFのRDFLib構文検証、語彙検証、pySHACL検証
- Workflow RDFの修正ループ
- 関連データ・ルール抽出AgentによるData RDF / Rule RDF生成
- 固定Data/Rule ontology、Data SHACL、Rule SHACLを参照したRDF生成
- Data RDFの生成、検証、修正ループ
- 検証済みData RDFを参照したRule RDFの生成、検証、修正ループ
- 実行結果のJSON / Turtle保存
- APIを呼ばないモックpytest

## 設計方針

- エージェント出力はPydanticによる構造化出力として受け取る。
- 説明的・設計的な出力は柔軟なJSONとして扱う。
- 各モードで必要なTurtle文字列はパイプライン側で明示的に確認する。
- Turtle本文はPydanticでは `str` として受け取り、内容の妥当性はRDFLibとpySHACLで検証する。
- LLMが生成したWorkflow/Data/Rule RDFをPython側で機械的に補完しない。
- シナリオJSONにない業務内容は推測せず、未確定事項として残す。
- ontologyとSHACL Shapesは事前定義TTLから読み込み、revisionではRDF本体だけを修正する。
- ontology / SHACLが存在しない場合は停止し、LLM生成へフォールバックしない。
- SQL、DB、Web UIは現時点では使用しない。

## ディレクトリ構成

```text
.
├── main.py
├── pyproject.toml
├── .env.example
├── README.md
├── ontology/
│   ├── workflow_ontology.ttl
│   ├── data_ontology.ttl
│   └── rule_ontology.ttl
├── shapes/
│   ├── workflow_shapes.ttl
│   ├── data_shapes.ttl
│   └── rule_shapes.ttl
├── src/
│   └── business_analysis_agents/
│       ├── __main__.py
│       ├── agents/
│       │   ├── scenario.py
│       │   ├── workflow.py
│       │   ├── data_rule.py
│       │   └── consistency.py
│       ├── controller.py
│       ├── config.py
│       ├── document_loader.py
│       ├── models.py
│       ├── rdf_validation.py
│       ├── workflow_pipeline.py
│       ├── data_rule_pipeline.py
│       ├── rdf/
│       ├── shacl/
│       └── review/
└── tests/
    ├── test_startup.py
    ├── test_scenario_generation.py
    ├── test_workflow_pipeline.py
    ├── test_data_rule_pipeline.py
    └── test_models.py
```

## セットアップ

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
Copy-Item .env.example .env
```

`.env` には次の値を設定します。

```env
OPENAI_API_KEY=
OPENAI_MODEL=
MAX_REPAIR_ITERATIONS=3
OUTPUT_DIR=outputs
```

`OPENAI_API_KEY` はコードに埋め込まず、環境変数または `.env` から読み込みます。

## 使い方

### 1. 起動確認

```powershell
python main.py
```

出力:

```text
システムを開始しました
```

### 2. PDFから業務シナリオJSONを生成

```powershell
python main.py inputs\sample.pdf
```

出力例:

```text
outputs/run_YYYYMMDD_HHMMSS/scenario.json
```

### 3. シナリオJSONからWorkflow RDFを生成

```powershell
python -m business_analysis_agents workflow --scenario outputs\run_YYYYMMDD_HHMMSS\scenario.json
```

デフォルト以外のWorkflow ontology / SHACL Shapesを使用する場合:

```powershell
python -m business_analysis_agents workflow `
  --scenario outputs\run_YYYYMMDD_HHMMSS\scenario.json `
  --ontology path\to\workflow_ontology.ttl `
  --shapes path\to\workflow_shapes.ttl
```

主な出力先:

```text
outputs/workflow/
```

主な出力ファイル:

- `workflow_ontology_v0_1.ttl`
- `workflow_shapes_v0_1.ttl`
- `workflow_ontology_design.json`
- `workflow_ontology_validation.json`
- `workflow_ontology_history.json`
- `workflow_final.ttl`
- `workflow_agent_output.json`
- `workflow_validation.json`
- `workflow_revision_history.json`
- `workflow_run_metadata.json`

### 4. シナリオJSONからData RDF / Rule RDFを生成

```powershell
python -m business_analysis_agents data-rule --scenario outputs\run_YYYYMMDD_HHMMSS\scenario.json
```

デフォルト以外の固定TTLを使用する場合:

```powershell
python -m business_analysis_agents data-rule `
  --scenario outputs\run_YYYYMMDD_HHMMSS\scenario.json `
  --data-ontology path\to\data_ontology.ttl `
  --rule-ontology path\to\rule_ontology.ttl `
  --data-shapes path\to\data_shapes.ttl `
  --rule-shapes path\to\rule_shapes.ttl
```

主な出力先:

```text
outputs/data_rule/
```

主な出力ファイル:

- `data_ontology_v0_1.ttl`
- `rule_ontology_v0_1.ttl`
- `data_shapes_v0_1.ttl`
- `rule_shapes_v0_1.ttl`
- `data_rule_ontology_design.json`
- `data_ontology_validation.json`
- `rule_ontology_validation.json`
- `data_rule_ontology_history.json`
- `data_initial.ttl`
- `data_final.ttl`
- `rule_initial.ttl`
- `rule_final.ttl`
- `data_agent_output.json`
- `rule_agent_output.json`
- `data_validation.json`
- `rule_validation.json`
- `data_revision_history.json`
- `rule_revision_history.json`
- `data_rule_run_metadata.json`

## エージェント構成

### Scenario Agent

PDFから抽出したページ番号付きテキストを入力し、固定フォーマットの業務シナリオJSONを生成します。

主な出力:

- 業務名
- 登場人物
- 業務目的
- 業務概要
- 業務手順
- 未確定事項

### Workflow Agent

外部的には1つのAIエージェントです。固定Ontology/SHACLを入力し、内部モードで処理を分けます。

- `workflow_generation`: シナリオJSONからWorkflow RDFを生成
- `workflow_revision`: 検証結果に基づきWorkflow RDFだけを修正

### 関連データ・ルール抽出Agent

外部的には1つのAIエージェントです。固定Ontology/SHACLを入力し、Data RDFとRule RDFを順番に処理します。

- `data_generation`: シナリオJSONからData RDFを生成
- `data_revision`: 検証結果に基づきData RDFだけを修正
- `rule_generation`: 検証済みData RDFを参照してRule RDFを生成
- `rule_revision`: 検証結果に基づきRule RDFだけを修正

## 検証

RDFの検証はLLMではなくPython側で行います。

- RDFLib: Turtle構文解析
- RDFLib: ontology / SHACL Shapesの構文検証
- RDFLib: 固定Ontologyで宣言されていないClass / Propertyの検出
- pySHACL: SHACL制約検証
- Python: 最大反復回数、終了条件、ontology / shapesのハッシュ固定確認

通常のpytestでは実APIを呼びません。

```powershell
python -m pytest
```

直近の確認結果:

```text
38 passed
```

## 未実装

- Workflow RDF / Data RDF / Rule RDF間のCross Review
- 3種類RDF全体の整合性評価エージェント
- 人間レビューUI
- 人間への問い合わせ生成UI
- DB保存
- BBO等を利用した本番用Workflow ontologyへの差し替え
- PDF再読み込みによるData/Rule補完
- Scenario Agent、Workflow Agentの大規模な再設計

## 注意

- `outputs/` は `.gitignore` 対象です。
- 機密PDFはリポジトリにコミットしないでください。
- 研究用プロトタイプのため、LLM出力が不安定な場合があります。
- LLMがJSONキーを重複出力した場合に備え、一部のAgent出力は復元処理を持っています。
