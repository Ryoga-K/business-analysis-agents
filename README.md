# business-analysis-agents

OpenAI Agents SDKを用いて、業務文書から業務知識を抽出し、Workflow RDF、Data RDF、Rule RDFを生成・検証する研究用プロトタイプです。

現在は、PDFと固定Scenario OntologyからScenario RDFを生成し、Workflow RDF、Data RDF、Rule RDFの生成・個別検証・修正、3 RDF間のCross Review、対話式Human Review、Human Reviewに基づく最終RDF確定までを1コマンドで実行できます。Web UIとDB保存はまだ実装していません。

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
- Controllerによる対象RDF単位のCross revision、個別SHACL再検証、Self-Review、Cross再検証
- Python ControllerによるPDFからHuman Review後の最終RDF確定までのEnd-to-End実行
- End-to-End実行のフェーズ・反復回数・API処理時間のCLI進捗表示とJSONLログ保存
- RDF・必須成果物が利用不能なfatal failure時の後続停止と`run_summary.json`への実行結果保存
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
- Consistency不適合時は対象Agent別に指示を集約して自動revisionし、同じ個別SHACLとCross-SHACLで再検証する。
- 最大Cross revision回数後も不適合の場合は実行エラーとせず、Human Reviewへ進む。
- Human Reviewで修正が承認されたfindingは対象Agent単位に集約し、targeted revisionでRDFへ反映する。
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
│       ├── progress.py
│       ├── self_review.py
│       ├── targeted_revision_pipeline.py
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

### 2. PDFから最終RDF確定までを一括実行

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
                 ├─ violation: targeted RDF revision
                 │   └─ individual SHACL + Self-Review + Cross recheck
                 └─ Human Review
                     └─ targeted revision + individual recheck + Cross recheck
                         └─ final RDF artifacts
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
- `outputs/consistency/consistency_revision_history.json`
- `outputs/human_review/human_review.json`
- `outputs/human_review/human_review_revision.json`
- `outputs/final/scenario_final.ttl`
- `outputs/final/workflow_final.ttl`
- `outputs/final/data_final.ttl`
- `outputs/final/rule_final.ttl`
- `outputs/final/final_summary.json`
- `outputs/controller/run_summary.json`
- `outputs/controller/progress.jsonl`

WorkflowまたはData/Ruleの個別SHACL・Self-Reviewが未適合でも、必須RDFが存在してTurtleとしてparseでき、後続処理に必要な成果物を読み込める場合はwarningとしてCross Consistencyへ進みます。自動Cross revision後もConsistencyが`needs_revision`の場合はHuman Reviewへ進みます。RDF・必須成果物の欠損、Turtle parse不能、入力読込失敗など、後続処理が技術的に実行できない場合だけfatal failureとして停止します。

E2E全体の`status`は、問題なく完了した`completed`、未解決事項を保持して最後まで完了した`completed_with_issues`、後続処理不能で停止した`fatal_failed`を区別します。`run_summary.json`にはRDF別のSHACL違反・Self-Review finding、Cross finding数、Human Review要否、fatal errorの有無を保存します。

Workflow、Data、Rule、Self-Review、Cross revision、targeted revisionの最大反復回数は、`src/business_analysis_agents/config.py`の`MAX_REVISION_ITERATIONS`を共通して使用します。最大回数を変更する場合は、この定数だけを変更してください。CLIや環境変数からの上書きは行いません。

実行中は、共通の進捗Reporterが大フェーズ、API呼び出し前後、検証結果、修正回数と修正対象を表示します。表示にはWindows端末でも扱いやすい`[RUN]`、`[OK]`、`[NG]`、`[WARN]`、`[REV]`、`[DONE]`を使用します。

```text
[Phase 2/6] Workflow RDF
  [RUN] Workflow RDF generation
  [OK] Workflow RDF generated (12.4 sec)
  [OK] Workflow SHACL validation passed
  [RUN] Workflow Self-Review
  [OK] Workflow Self-Review passed
  [DONE] Workflow completed

[Phase 5/6] Cross Consistency
  [NG] Cross validation found 3 violation(s)
  [REV] Cross revision 1/3 [target: rule]
  [RUN] Cross re-validation 1/3
  [DONE] Cross Consistency passed
```

同じイベントは`outputs/controller/progress.jsonl`にも保存します。各行には`timestamp`、`phase`、`step`、`status`、`iteration`、`max_iterations`、`duration`、`message`、`target`を記録し、APIキー、PDF本文、プロンプト全文は含めません。

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

Self-Review revision後も、raw RDF生成後に作成した同じ`workflow_shapes_generated.ttl`で再検証します。SHACL revisionとSelf-Review revisionの最大回数には共通の`MAX_REVISION_ITERATIONS`を使用します。

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

Data/RuleのSHACL revisionとSelf-Review revisionにも、共通の`MAX_REVISION_ITERATIONS`を使用します。

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

個別`consistency` CLIはCross検証と違反分析までを実行します。対象RDFの自動revisionループは、PDFとScenario RDFを参照できる統合CLIの`run --pdf`で実行します。

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

RDFLibで3 RDFと3 Ontologyをそれぞれ統合してpySHACL検証します。現在の固定Ontologyと生成済みRDFには3 RDF間の直接URI参照がないため、名称類似だけを根拠とした対応付けは行いません。

Controllerでは、違反分析を`workflow`、`data`、`rule`ごとに集約し、対象RDFにつき1回のrevisionを実行します。revision後は生成済みの個別SHACLで検証し、Self-Reviewを通過した場合だけCross検証へ戻します。Cross-SHACLは初回に1回だけ生成し、以後は初回ハッシュと一致する同じTTLを再利用します。各反復は`consistency_revision_history.json`へ保存します。

### Human Review

E2E実行では、Human Reviewで「指摘を承認」または「補足情報を入力」と判断されたfindingを`target_agent`ごとに集約し、既存のtargeted revisionへ1回ずつ渡します。「現在のRDFを承認」は修正せず、「保留」は未解決として扱います。

Human Review revision後は、実行中に生成・固定した同じ個別SHACLで対象RDFを再検証し、Self-Reviewを実行します。未解決の品質問題が残ってもRDFが利用可能なら、同じCross-SHACLと保存済みハッシュを使ってCross Consistencyを1回再検証します。Human Reviewは繰り返しません。

確定したScenario / Workflow / Data / Rule RDFは`outputs/final/`へ保存します。`final_summary.json`には、最終状態、各RDFのSHACL適合状態、Self-Review状態、Cross Consistency状態、Human Review revisionの有無、revision回数、最終RDFハッシュを記録します。

最終状態は次のいずれかです。

- `completed_without_human_revision`
- `completed_after_human_revision`
- `unresolved_after_human_review`
- `individual_validation_failed_after_human_review`
- `pipeline_failed`

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
79 passed
```

## 未実装

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
