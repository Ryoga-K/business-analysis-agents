# business-analysis-agents

OpenAI Agents SDKを用いて、業務文書から業務知識を抽出し、Workflow RDF、Data RDF、Rule RDFを生成・検証する研究用プロトタイプです。

現在は、PDFと固定Scenario OntologyからScenario RDFを生成し、Workflow RDF、Data RDF、Rule RDFの生成・個別検証・修正と、3 RDF間のCross Reviewまで実装しています。Web UI、DB保存、人間レビューUIはまだ実装していません。

## 実装済み

- PyMuPDFによるPDFテキストのページ単位抽出
- 固定Scenario Ontologyを参照したScenario RDFのTurtle直接生成
- 元PDFとScenario RDFの両方を参照したWorkflow/Data/Rule RDF生成・修正
- Workflow AgentによるWorkflow RDF生成
- 固定Workflow ontologyを参照したWorkflow RDF生成とAIによるSHACL Shapes生成
- Workflow RDFのRDFLib構文検証、語彙検証、pySHACL検証
- Workflow RDFの修正ループ
- 関連データ・ルール抽出AgentによるData RDF / Rule RDF生成
- 固定Data/Rule ontologyを参照したRDF生成とData/Rule別のSHACL Shapes生成
- Data RDFの生成、検証、修正ループ
- 検証済みData RDFを参照したRule RDFの生成、検証、修正ループ
- AI生成Cross-SHACLによるWorkflow/Data/Rule RDF間の整合性検証
- Cross-SHACL違反の原因、修正対象Agent、修正指示の構造化出力
- 実行結果のJSON / Turtle保存
- APIを呼ばないモックpytest

## 設計方針

- エージェント出力はPydanticによる構造化出力として受け取る。
- 説明的・設計的な出力は柔軟なJSONとして扱う。
- 各モードで必要なTurtle文字列はパイプライン側で明示的に確認する。
- Turtle本文はPydanticでは `str` として受け取り、内容の妥当性はRDFLibとpySHACLで検証する。
- LLMが生成したWorkflow/Data/Rule RDFをPython側で機械的に補完しない。
- PDFとScenario RDFのどちらにもない業務内容は推測しない。
- ontologyは事前定義TTLから読み込み、実行中は変更しない。
- SHACL Shapesは各raw RDFの生成後にAIが1回だけ生成し、ハッシュ固定してrevisionではRDF本体だけを修正する。
- Cross-SHACLも1回だけ生成・固定し、整合性の適合判定は統合Graphに対するpySHACLで行う。
- ontologyが存在しない場合は停止し、LLM生成へフォールバックしない。
- SQL、DB、Web UIは現時点では使用しない。

## ディレクトリ構成

```text
.
├── main.py
├── pyproject.toml
├── .env.example
├── README.md
├── ontology/
│   ├── scenario_ontology.ttl
│   ├── workflow_ontology.ttl
│   ├── data_ontology.ttl
│   └── rule_ontology.ttl
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
│       ├── consistency_pipeline.py
│       ├── rdf/
│       ├── shacl/
│       └── review/
└── tests/
    ├── test_startup.py
    ├── test_scenario_generation.py
    ├── test_workflow_pipeline.py
    ├── test_data_rule_pipeline.py
    ├── test_consistency_pipeline.py
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

### 2. PDFからScenario RDFを生成

```powershell
python main.py inputs\sample.pdf
```

出力例:

```text
outputs/scenario/scenario_final.ttl
```

### 3. PDFとScenario RDFからWorkflow RDFを生成

```powershell
python -m business_analysis_agents workflow --pdf inputs\sample.pdf --scenario outputs\scenario\scenario_final.ttl
```

デフォルト以外のWorkflow ontologyを使用する場合:

```powershell
python -m business_analysis_agents workflow `
  --pdf inputs\sample.pdf `
  --scenario outputs\scenario\scenario_final.ttl `
  --ontology path\to\workflow_ontology.ttl
```

主な出力先:

```text
outputs/workflow/
```

通常実行時の出力ファイル:

- `workflow_final.ttl`
- `workflow_shapes_generated.ttl`
- `workflow_validation.json`
- `workflow_revision_history.json`

固定Ontology、生成SHACLの検証結果やAgent出力などの詳細ファイルも保存する場合:

```powershell
python -m business_analysis_agents workflow `
  --pdf inputs\sample.pdf `
  --scenario outputs\scenario\scenario_final.ttl `
  --save-debug-outputs
```

### 4. PDFとScenario RDFからData RDF / Rule RDFを生成

```powershell
python -m business_analysis_agents data-rule --pdf inputs\sample.pdf --scenario outputs\scenario\scenario_final.ttl
```

デフォルト以外の固定TTLを使用する場合:

```powershell
python -m business_analysis_agents data-rule `
  --pdf inputs\sample.pdf `
  --scenario outputs\scenario\scenario_final.ttl `
  --data-ontology path\to\data_ontology.ttl `
  --rule-ontology path\to\rule_ontology.ttl
```

主な出力先:

```text
outputs/data_rule/
```

主な出力ファイル:

- `data_final.ttl`
- `data_shapes_generated.ttl`
- `data_validation.json`
- `data_revision_history.json`
- `rule_final.ttl`
- `rule_shapes_generated.ttl`
- `rule_validation.json`
- `rule_revision_history.json`

### 5. Workflow / Data / Rule RDF間のCross Reviewを実行

```powershell
python -m business_analysis_agents consistency
```

デフォルトでは、次の検証済みRDFを読み込みます。

- `outputs/workflow/workflow_final.ttl`
- `outputs/data_rule/data_final.ttl`
- `outputs/data_rule/rule_final.ttl`

対応する`workflow_validation.json`、`data_validation.json`、`rule_validation.json`の`conforms=true`も実行前に確認します。

主な出力ファイル:

- `outputs/consistency/consistency_shapes_generated.ttl`
- `outputs/consistency/consistency_validation.json`
- `outputs/consistency/consistency_evaluation.json`

入力RDFやOntologyを変更する場合:

```powershell
python -m business_analysis_agents consistency `
  --workflow path\to\workflow_final.ttl `
  --data path\to\data_final.ttl `
  --rule path\to\rule_final.ttl `
  --workflow-validation path\to\workflow_validation.json `
  --data-validation path\to\data_validation.json `
  --rule-validation path\to\rule_validation.json `
  --workflow-ontology path\to\workflow_ontology.ttl `
  --data-ontology path\to\data_ontology.ttl `
  --rule-ontology path\to\rule_ontology.ttl
```

## エージェント構成

### Scenario Agent

PDFから抽出したページ番号付きテキストと固定 `ontology/scenario_ontology.ttl` を入力し、Scenario RDFの完全なTurtle文字列を直接生成します。Scenario Ontologyは実行時に変更しません。Scenario RDFには今回、SHACL検証、語彙検証、修正ループを適用しません。

主な出力要素:

- 型を付けない業務Subject
- `prov:Agent` として表すActor
- `prov:Activity` として表すUseCase
- `dcmitype:Collection` として表すPackage
- `dcterms:hasPart` と `prov:wasAssociatedWith` による関係

### Workflow Agent

外部的には1つのAIエージェントです。固定Ontologyを入力し、内部モードで処理を分けます。

- `workflow_generation`: PDFとScenario RDFからWorkflow RDFを生成
- `workflow_shacl_generation`: raw Workflow RDFと固定OntologyからSHACL Shapesを生成
- `workflow_revision`: PDF、Scenario RDF、検証結果に基づきWorkflow RDFだけを修正

### 関連データ・ルール抽出Agent

外部的には1つのAIエージェントです。固定Ontologyを入力し、Data RDFとRule RDFを順番に処理します。

- `data_generation`: PDFとScenario RDFからData RDFを生成
- `data_shacl_generation`: raw Data RDFと固定Data OntologyからData SHACLを生成
- `data_revision`: PDF、Scenario RDF、検証結果に基づきData RDFだけを修正
- `rule_generation`: PDF、Scenario RDF、検証済みData RDFからRule RDFを生成
- `rule_shacl_generation`: raw Rule RDFと固定Rule OntologyからRule SHACLを生成
- `rule_revision`: PDF、Scenario RDF、検証結果に基づきRule RDFだけを修正

### Consistency Agent

Workflow/Data/Rule RDFと3つの固定Ontologyを入力し、次の2モードで処理します。

- `cross_shacl_generation`: 明示されたRDF間参照・型・URI整合性を検証するCross-SHACLを生成
- `violation_analysis`: pySHACL違反の原因、修正対象Agent、RDF修正指示を構造化

RDFLibで3 RDFと3 Ontologyをそれぞれ統合してpySHACL検証します。現在の固定Ontologyと生成済みRDFには3 RDF間の直接URI参照がないため、名称類似だけを根拠とした対応付けは行いません。Consistency Agentは修正指示までを出力し、抽出Agentの自動再実行は行いません。

## 検証

RDFの検証はLLMではなくPython側で行います。

- RDFLib: Turtle構文解析
- RDFLib: 固定ontology / AI生成SHACL Shapesの構文・参照整合性検証
- RDFLib: 固定Ontologyで宣言されていないClass / Propertyの検出
- pySHACL: SHACL制約検証
- pySHACL: 3 RDF統合Graphに対するCross-SHACL検証
- Python: 最大反復回数、終了条件、ontology / shapesのハッシュ固定確認

通常のpytestでは実APIを呼びません。

```powershell
python -m pytest
```

直近の確認結果:

```text
49 passed
```

## 未実装

- Consistency Agentから抽出Agentを自動再実行する修正ループ
- Consistency AgentのSelf-Review
- 人間レビューUI
- 人間への問い合わせ生成UI
- DB保存
- BBO等を利用した本番用Workflow ontologyへの差し替え
- Scenario Agent、Workflow Agentの大規模な再設計

## 注意

- `outputs/` は `.gitignore` 対象です。
- 機密PDFはリポジトリにコミットしないでください。
- 研究用プロトタイプのため、LLM出力が不安定な場合があります。
- LLMがJSONキーを重複出力した場合に備え、一部のAgent出力は復元処理を持っています。
