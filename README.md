# business-analysis-agents

OpenAI Agents SDKを用いて、業務文書から業務知識を抽出し、Workflow RDF、Data RDF、Rule RDFを生成・検証する研究用プロトタイプです。

現在は、PDFと固定Scenario OntologyからScenario RDFを生成し、Workflow RDF、Data RDF、Rule RDFの生成・個別検証・修正、3 RDF間のCross Review、対話式Human Reviewまでを1コマンドで実行できます。Web UIとDB保存はまだ実装していません。

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
- Python ControllerによるPDFからConsistency評価までのEnd-to-End実行
- 工程失敗時の後続停止と`run_summary.json`への実行結果保存
- SHACL構造に基づくConsistency findingのグループレビューと構造化結果保存
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
- Workflow/Data/RuleはSHACL適合後にPDF・Scenario RDF・対象RDFをAIでSelf-Reviewし、意味的な欠落・矛盾・誤抽出があればRDFだけを再修正する。
- Cross-SHACLも1回だけ生成・固定し、整合性の適合判定は統合Graphに対するpySHACLで行う。
- End-to-End ControllerはAI判断を行わず、既存pipelineを決められた順序で呼び出す。
- Consistencyの不適合は実行エラーとせず、評価結果を保存して正常終了する。
- Human ReviewはRDFやAgentを再実行せず、人間の判断と補足情報だけを保存する。
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
    ├── test_end_to_end_controller.py
    ├── test_human_review.py
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

### 2. PDFからHuman Reviewまでを一括実行

```powershell
python -m business_analysis_agents run --pdf inputs\sample.pdf
```

次の順番で既存pipelineを実行します。

```text
PDF
 └─ Scenario RDF
     └─ Workflow RDF + individual validation + Self-Review
         └─ Data RDF / Rule RDF + individual validation + Self-Review
             └─ Cross-SHACL validation + Consistency evaluation
                 └─ Human Review
```

主な出力:

- `outputs/scenario/scenario_final.ttl`
- `outputs/workflow/workflow_final.ttl`
- `outputs/workflow/workflow_self_review.json`
- `outputs/data_rule/data_final.ttl`
- `outputs/data_rule/rule_final.ttl`
- `outputs/data_rule/data_self_review.json`
- `outputs/data_rule/rule_self_review.json`
- `outputs/consistency/consistency_evaluation.json`
- `outputs/human_review/human_review.json`
- `outputs/controller/run_summary.json`

WorkflowまたはData/Ruleの個別検証が未適合の場合と、工程内で例外が発生した場合は後続工程を実行しません。Consistencyが`needs_revision`を返した場合もHuman Reviewへ進みます。findingや人間の判断内容はシステムエラーとせず、Human Reviewの入出力処理が失敗した場合だけ全体を失敗とします。

### 3. PDFからScenario RDFを生成

```powershell
python main.py inputs\sample.pdf
```

出力例:

```text
outputs/scenario/scenario_final.ttl
```

### 4. PDFとScenario RDFからWorkflow RDFを生成

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
- `workflow_self_review.json`

Self-Reviewによる修正回数は`--max-self-review-iterations`で変更できます。Self-Review revision後も、raw RDF生成後に作成した同じ`workflow_shapes_generated.ttl`で再検証します。

固定Ontology、生成SHACLの検証結果やAgent出力などの詳細ファイルも保存する場合:

```powershell
python -m business_analysis_agents workflow `
  --pdf inputs\sample.pdf `
  --scenario outputs\scenario\scenario_final.ttl `
  --save-debug-outputs
```

### 5. PDFとScenario RDFからData RDF / Rule RDFを生成

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
- `data_self_review.json`
- `rule_final.ttl`
- `rule_shapes_generated.ttl`
- `rule_validation.json`
- `rule_revision_history.json`
- `rule_self_review.json`

Self-Reviewによる修正回数は`--max-data-self-review-iterations`と`--max-rule-self-review-iterations`で個別に変更できます。

### 6. Workflow / Data / Rule RDF間のCross Reviewを実行

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

### 7. Consistency findingをHuman Reviewする

```powershell
python -m business_analysis_agents human-review
```

デフォルトでは既存のWorkflow/Data/Rule RDFと`outputs/consistency/consistency_evaluation.json`を読み込みます。findingは修正対象Agent、`resultPath`、SHACL constraint component、source shape、severityの組み合わせでグループ化します。自然言語の文章類似度は使用しません。

各グループについて、一括で指摘を承認、現在のRDFを承認、補足情報を入力、個別に確認、保留から選択します。個別確認を選んだ場合だけ、従来どおりグループ内のfindingを1件ずつ表示します。

出力:

```text
outputs/human_review/human_review.json
```

入力・出力を変更する場合:

```powershell
python -m business_analysis_agents human-review `
  --workflow path\to\workflow_final.ttl `
  --data path\to\data_final.ttl `
  --rule path\to\rule_final.ttl `
  --consistency-evaluation path\to\consistency_evaluation.json `
  --output path\to\human_review.json `
  --reviewer reviewer-name
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
- `workflow_self_review`: PDF、Scenario RDF、現在のWorkflow RDFを比較し、意味的な欠落・矛盾・誤抽出を構造化して評価

### 関連データ・ルール抽出Agent

外部的には1つのAIエージェントです。固定Ontologyを入力し、Data RDFとRule RDFを順番に処理します。

- `data_generation`: PDFとScenario RDFからData RDFを生成
- `data_shacl_generation`: raw Data RDFと固定Data OntologyからData SHACLを生成
- `data_revision`: PDF、Scenario RDF、検証結果に基づきData RDFだけを修正
- `data_self_review`: PDF、Scenario RDF、現在のData RDFを比較して意味的に評価
- `rule_generation`: PDF、Scenario RDF、検証済みData RDFからRule RDFを生成
- `rule_shacl_generation`: raw Rule RDFと固定Rule OntologyからRule SHACLを生成
- `rule_revision`: PDF、Scenario RDF、検証結果に基づきRule RDFだけを修正
- `rule_self_review`: PDF、Scenario RDF、検証済みData RDF、現在のRule RDFを比較して意味的に評価

### Consistency Agent

Workflow/Data/Rule RDFと3つの固定Ontologyを入力し、次の2モードで処理します。

- `cross_shacl_generation`: 明示されたRDF間参照・型・URI整合性を検証するCross-SHACLを生成
- `violation_analysis`: pySHACL違反の原因、修正対象Agent、RDF修正指示を構造化

RDFLibで3 RDFと3 Ontologyをそれぞれ統合してpySHACL検証します。現在の固定Ontologyと生成済みRDFには3 RDF間の直接URI参照がないため、名称類似だけを根拠とした対応付けは行いません。Consistency Agentは修正指示までを出力し、抽出Agentの自動再実行は行いません。

### Human Review

Human ReviewはAI Agentではなく対話式CLIです。Consistencyのviolationと解析結果を`violation_index`で対応付けた後、SHACLの構造化情報でグループ化します。Consistencyが適合している場合は入力を求めず、「確認事項なし」として正常終了します。

`human_review.json`の`groups`には、グループキー、finding ID一覧、違反タイプ、対象Agent、共通原因、共通修正方針、対象リソース、グループ判断、元finding一覧を保存します。個別確認した場合だけ`individual_results`に個別判断を保存します。トップレベルの`findings`には一括判断を展開した結果も含め、将来の修正ループからfinding単位で利用できるようにしています。

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
63 passed
```

## 未実装

- Consistency Agentから抽出Agentを自動再実行する修正ループ
- Consistency AgentのSelf-Review
- Human Review Web UI
- 人間への問い合わせ生成UI
- DB保存
- BBO等を利用した本番用Workflow ontologyへの差し替え
- Scenario Agent、Workflow Agentの大規模な再設計

## 注意

- `outputs/` は `.gitignore` 対象です。
- 機密PDFはリポジトリにコミットしないでください。
- 研究用プロトタイプのため、LLM出力が不安定な場合があります。
- LLMがJSONキーを重複出力した場合に備え、一部のAgent出力は復元処理を持っています。
