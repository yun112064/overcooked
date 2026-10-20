# 驗收矩陣（AC01–AC32、G01–G07）

- Ticket：`tickets/20-acceptance-handoff.md`；Spec：`spec.md`
- 依據版本：整合分支 `feature/t11-t20` @ `870230f`（2026-10-20）
- **結論：MVP 尚未完成。** AC28（主驗收）未通過；G01–G07 沒有一項完整通過。Ticket 18（五人負載、月費）與 Ticket 19（真人 QA）的實測都還沒做，正式環境（Cloudflare Tunnel）也還沒建立。本矩陣只整理現有證據，**不是 Ticket 20 的完成宣告**。

## 判定規則

| 狀態 | 意義 |
|---|---|
| 通過-自動化 | 條件本身是程式行為（計算、權限、交易、狀態），自動化測試直接證明。**只代表程式邏輯正確**，不等於正式環境或真實資料驗收；正式交付前仍要經 T19 實際環境複驗。 |
| 部分 | 有一部分證據，但缺真實資料、缺某一面（例如前端）、或有已知未修問題。 |
| 未驗證-需實際環境 | 條件只能在部署後的真實環境量測（外網、跨節點、真實報價）；目前只有工具或合成測試。 |
| 未驗證-需真人 | 條件要求真人測試者樣本，目前 0 筆。 |
| 未完成 | 已知未達標、違反 spec、或必要證據不存在。 |

| 證據類型 | 意義 |
|---|---|
| automated-synthetic | 自動化測試，使用合成資料／假的外部服務（多數接真實 PostgreSQL 測試庫） |
| real-data | 用真實來源資料執行（真實工作簿、真實網站少量請求） |
| real-environment | 在正式部署環境量測 |
| human | 真人操作或人工審核 |

- 測試名稱以 `檔案::測試` 表示，檔案都在 `tests/`。撰寫本文件時沒有重跑測試，狀態依各 ticket 合併時的測試結果；交付前須在 CI 或本機（`TEST_DATABASE_URL` 指向可清空的測試庫）重跑一次並附輸出。
- 「已知問題 #n」指交付前審查清單，見 [limitations.md](limitations.md) 第 3 節。
- 合成測試**一律不算真實功能驗收**（ticket 20 驗收條件）。

## 驗收條件 AC01–AC32

| AC | 要求摘要 | 責任 ticket | 證據 | 證據類型 | 狀態 | 阻擋項目 |
|---|---|---|---|---|---|---|
| AC01 | 註冊、登入、登出、維護家庭／成員；跨家庭讀寫、工作、圖片存取被拒 | T07、T09、T10、T16、T19 | `test_t07_identity.py`::test_register_sets_secure_httponly_cookie_and_hashes_password、test_login_logout_and_wrong_password、test_household_create_select_and_isolation、test_cross_household_member_access_is_denied；`test_t08_meal_requests.py`::test_cross_household_access_is_denied；`test_t09_recognition.py`::test_cross_household_access_is_denied；`test_t10_jobs.py`::test_jobs_are_household_scoped；`test_t15_meal_results.py`::test_results_are_household_scoped；`test_t16_meal_plans.py`::test_cross_household_adoption_and_reads_are_rejected | automated-synthetic | 通過-自動化 | T19 實際環境（外網 HTTPS）複驗未做 |
| AC02 | 成人目標三選一；n 只接受五種值且前後端都拒絕；未成年不可增肌／減脂；過敏／禁吃套用 | T03、T07、T08 | `test_t07_identity.py`::test_only_five_portions_are_accepted、test_minor_cannot_use_goal_adjustments、test_fat_loss_below_minimum_is_reported_not_clamped、test_member_crud_with_restrictions_and_versioning；`test_t08_meal_requests.py`::test_restrictions_are_union_of_participants、test_synonym_foods_are_also_restricted；`test_t12_meal_calculation.py`::test_individual_n_must_be_one_of_five_options_but_total_n_is_free；`nutrition_target/tests` | automated-synthetic | 部分 | 前端拒絕沒有自動化測試（`frontend/` 無測試檔），需 T19 手動確認；營養規則本身屬 G03（未通過） |
| AC03 | 問卷保存／返回／修改；便當另列、預設午餐可改；30／30／40 | T08 | `test_t08_meal_requests.py`::test_partial_draft_saves_and_reports_missing_items、test_update_keeps_versions_and_rejects_stale_edit、test_invalid_input_is_rejected_without_losing_saved_draft、test_dining_and_bento_are_separate_items_and_bento_defaults_to_lunch、test_target_preview_uses_meal_ratios_and_each_item_once、test_breakfast_ratio_is_thirty_percent | automated-synthetic | 通過-自動化 | T19 複驗 |
| AC04 | 每個有效蔬菜各一個可編輯框、可修正／略過／補充；同類只列一次；無結果提示手動；不要求重量 | T09 | `test_t09_recognition.py`::test_all_valid_vegetables_are_listed_once_by_confidence、test_every_detected_vegetable_is_returned、test_no_valid_detection_asks_for_manual_input、test_confirm_records_corrected_type_without_weight、test_one_photo_confirms_several_vegetables、test_unavailable_service_keeps_photo_and_can_retry | automated-synthetic（假辨識器） | 通過-自動化 | 真實 YOLO 經 Cloudflare Tunnel 的端到端辨識未驗證（T17 正式環境）；模型精度屬 AC22 |
| AC05 | 已匯入食譜有來源、授權／版本、用量、步驟；報告 150 道目標的取得、去重、合格數 | T01 | `docs/T01-recipe-database.md`「真實資料匯入結果」：`recipe_dataset_1017.xlsx`（SHA-256 `6bcaca41…7c304`）293 道、合格 281 道、未合格 12 道與原因；`test_t01_recipe_import.py` 22 項 | real-data＋automated-synthetic | 部分 | 授權文件編號待補、審核人姓名待補（`config/recipe_sources.json`、`config/recipe_reviews.json`）；審核是「團隊確認全部已審核」的整批紀錄，缺逐道人工抽查紀錄 |
| AC06 | ≥20 個固定查詢情境（食材、指定菜、別名、禁忌、昨日排除、查無）符合人工預期；食譜不建向量索引 | T11 | `test_t11_recipe_retrieval.py`::test_fixed_scenarios（`SCENARIOS` ≥20）、test_candidates_exclude_restricted_previous_day_and_ineligible_recipes、test_empty_recipe_database_is_no_match_not_error、test_database_failure_is_an_error_not_no_result；T01 資料表無 vector 欄位 | automated-synthetic（合成食譜） | 部分 | 固定情境只在合成食譜上跑；尚未在真實 281 道語料上以人工預期逐項核對（G01／G05） |
| AC07 | 查無／去重後無可行資料庫菜單時不再徵求同意即轉 AI 並標示；資料庫模式不新增庫外菜；查詢故障不當查無 | T11、T14、T15 | `test_t14_generation.py`::test_no_feasible_database_menu_falls_back_to_ai_without_consent、test_database_menu_uses_only_retrieved_dishes_without_calling_the_model、test_database_failure_is_retried_and_never_falls_back_to_ai；`test_t11_recipe_retrieval.py`::test_no_feasible_result_is_fallback_with_context；`test_t15_meal_results.py`::test_ai_result_is_labelled_with_readable_fallback_reason；`test_t16_meal_plans.py`::test_ai_menu_adoption_keeps_ai_label_fallback_reason_and_knowledge_citations | automated-synthetic（假模型） | 部分 | 已知問題 #2：資料庫模式只取排名第一組合，營養不符時不試其他組合也不回退 AI，與「無可行資料庫菜單」的語意不符 |
| AC08 | Structured Outputs 仍經後端檢查；拒答、截斷、虛構來源、不合法食材不能採用；重試不丟問卷 | T10、T14、T15 | `test_t14_generation.py`::test_refusal_is_retried_once_then_reported_without_more_calls、test_truncated_and_malformed_outputs_are_rejected、test_unknown_ingredient_and_forbidden_ingredient_are_rejected、test_model_ingredient_id_contradicting_its_name_is_rejected、test_only_citations_matching_retrieved_knowledge_are_kept；`test_t14_llm_client.py` 7 項；`test_t15_meal_results.py`::test_result_list_and_failure_keeps_questionnaire | automated-synthetic（假模型） | 通過-自動化 | 未以真實 `gpt-5.4-mini-2026-03-17` 呼叫驗證輸出品質（屬 G05） |
| AC09 | 指定菜計入菜數，衝突時說明原因；過敏／禁吃不因偏好、缺值或直接生成而放寬 | T08、T11、T14、T15 | `test_t08_meal_requests.py`::test_specified_dishes_count_toward_dish_count；`test_t14_generation.py`::test_ai_candidate_with_allergen_is_rejected_even_under_synonym、test_specified_dish_missing_from_database_triggers_ai_and_must_appear、test_ambiguous_specified_dish_is_reported_not_guessed；`test_t15_meal_validation.py`::test_hard_constraint_failure_blocks | automated-synthetic | 未完成 | **已知問題 #1（違反 spec）**：指定菜含禁忌／過敏原時以 `SPECIFIED_DISH_DIETARY_CONFLICT` 自動 AI 回退，生出同名換料的菜，而非回報衝突、不換菜 |
| AC10 | 600／30、n=1.3 → 780／39；先基準再縮放，以 780／39 ±10% 判定；五種 n 都有案例 | T12 | `test_t12_meal_calculation.py`::test_600_30_with_n_1_3_gives_final_target_780_39、test_baseline_600_30_scaled_by_1_3_is_judged_against_780_39、test_all_five_portion_options_hand_checked、test_final_is_recalculated_and_checked_against_scaled_target | automated-synthetic | 通過-自動化 | — （算術驗證；營養規則正式性屬 G03） |
| AC11 | 多人不同基準與 n、同人另帶便當，合成目標符合 R06，無重複縮放或遺漏 | T08、T12 | `test_t12_meal_calculation.py`::test_members_with_different_baselines_and_bento_are_each_scaled_once、test_targets_from_t03_aggregate_match_its_participation_snapshots；`test_t08_meal_requests.py`::test_only_bento_member_counts_and_non_participants_are_excluded | automated-synthetic | 通過-自動化 | — |
| AC12 | 熱量、蛋白質各自 90%／110% 含邊界通過，超出不可採用；調整後重算，不以取整逃過 | T12、T15 | `test_t12_meal_calculation.py`::test_90_and_110_percent_pass_inclusive_for_each_nutrient、test_just_outside_either_bound_is_not_adoptable_even_if_display_rounds_inside、test_non_terminating_reference_is_compared_exactly、test_both_energy_and_protein_must_fit_at_once、test_display_grams_are_rounded_only_for_display；`test_t16_meal_plans.py`::test_nutrition_out_of_range_is_rejected | automated-synthetic | 通過-自動化 | — |
| AC13 | 生／熟、可食部可追溯；熟食不重複修正；依據缺失呈現缺營養並阻擋 | T04、T12 | `test_t12_meal_calculation.py`::test_cooked_tfda_record_with_cooked_weight_is_traceable_and_not_corrected_again、test_cooked_record_given_as_raw_weight_is_rejected、test_raw_food_without_approved_cooking_basis_is_incomplete_not_zero、test_traceability_is_kept_per_ingredient；`nutrition_model/` 53 項（`docs/T04-acceptance-2026-10-16.md`） | automated-synthetic＋real-data（TFDA 工作簿忠實匯入） | 部分 | G04 未通過：烹調係數用途 P01 待決議、沒有人工對照案例；資料庫未保存各食材生熟狀態（一律假設生重）；真實食譜的可計算覆蓋率未量測 |
| AC14 | 100 g、300 g／60 元 → 採購 60、成本 20；已有食材缺量與成本 0、不查價、不列清單、營養照算；全有時兩總價 0 | T13、T15 | `test_t13_meal_cost.py`::test_100g_needed_from_300g_pack_at_60_buys_60_and_costs_20、test_end_to_end_100g_from_300g_pack、test_owned_ingredient_has_zero_shortage_no_lookup_and_is_not_listed、test_all_owned_gives_both_totals_zero；`test_t15_meal_results.py`::test_owned_ingredients_are_not_priced_but_still_count_for_nutrition | automated-synthetic | 通過-自動化 | 真實價格覆蓋屬 G02 |
| AC15 | 成本不超預算即合格，遠低於預算仍通過；整包支出不作比較值；餘料不加營養 | T13、T15 | `test_t13_meal_cost.py`::test_far_below_budget_still_passes、test_cost_above_budget_is_exceeded、test_packs_round_up_but_cost_stays_proportional；`test_t15_meal_validation.py`::test_complete_within_budget_and_nutrition_is_adoptable | automated-synthetic | 通過-自動化 | — |
| AC16 | 缺價可重試、可明確接受後採用並保存標示；已知成本超預算仍禁止 | T13、T15、T16 | `test_t15_meal_results.py`::test_missing_price_awaits_decision_then_explicit_acceptance_makes_it_adoptable、test_price_retry_rereads_prices_into_a_new_result_version、test_price_retry_still_missing_needs_a_fresh_acceptance、test_known_cost_over_budget_cannot_be_accepted；`test_t16_meal_plans.py`::test_missing_price_needs_acceptance_record_and_saves_the_exception_flag、test_known_cost_over_budget_is_rejected_by_the_backend；`test_t13_meal_cost.py`::test_known_costs_over_budget_win_over_unknown | automated-synthetic | 通過-自動化 | 缺價重試只重讀資料庫快照，不即時查價（見 limitations） |
| AC17 | 缺必要營養可生成查看但不可採用；補齊重驗後才可；未知不填 0 | T12、T15、T16 | `test_t12_meal_calculation.py`::test_missing_required_nutrient_blocks_until_filled_and_revalidated、test_unresolved_weight_blocks_adoption_and_is_not_zero；`test_t15_meal_validation.py`::test_missing_required_nutrition_is_viewable_but_not_adoptable_even_after_continue；`test_t15_meal_results.py`::test_missing_required_nutrition_is_viewable_but_never_adoptable | automated-synthetic | 通過-自動化 | — |
| AC18 | 同冪等命令重送不重複；改內容重用鍵被拒；版本不符不採用錯誤版本 | T10、T15、T16 | `test_t10_jobs.py`::test_same_key_same_content_returns_same_job、test_reused_key_with_different_content_is_rejected、test_concurrent_submits_with_same_key_create_one_job；`test_t16_meal_plans.py`::test_same_key_same_command_saves_once_and_different_content_is_rejected、test_wrong_or_stale_versions_are_rejected_without_history、test_a_confirmed_request_cannot_adopt_another_result_version；`test_t15_meal_results.py`::test_decision_on_a_stale_questionnaire_version_is_rejected | automated-synthetic | 通過-自動化 | — |
| AC19 | 採用交易失敗全回滾；可按家庭、日期回看至少兩日完整快照；未採用不算歷史 | T16 | `test_t16_meal_plans.py`::test_failure_inside_the_transaction_rolls_everything_back、test_adopting_an_eligible_result_saves_a_complete_snapshot_and_confirms_the_request、test_adopt_0819_then_0820_generation_excludes_yesterday_except_specified、test_generation_and_viewing_do_not_add_history、test_source_updates_do_not_change_adopted_snapshots | automated-synthetic | 通過-自動化 | T19 歷史頁真人複驗 |
| AC20 | 背景工作重啟後可恢復或明確需重試；不確定已計費不無限重送；費用可追查 | T10 | `test_t10_jobs.py`::test_expired_lease_is_recovered_then_fails_after_limit、test_recovery_with_in_flight_paid_call_requires_manual_retry、test_uncertain_paid_call_in_handler_is_not_retried、test_retry_is_limited_to_two_attempts、test_settlement_uses_actual_usage、test_scheduler_is_single_instance | automated-synthetic | 通過-自動化 | 正式環境未實際重啟演練 |
| AC21 | 5 人同時：API p95 ≤1 s、暖機辨識 p95 ≤10 s、生成含排隊 p95 ≤90 s；記錄樣本、機型、輸入、來源狀態 | T18 | 工具：`scripts/load/five_user_load.py`；`test_t18_load_runner.py`、`test_t18_load_report.py`（dry-run 永不判通過）；`docs/T18-load-and-cost.md` 第 1 節表格**空白** | automated-synthetic（只驗工具） | 未驗證-需實際環境 | T17 正式部署、團隊電腦上線、真實照片與情境、OpenAI 額度 |
| AC22 | YOLO 獨立測試集主要類別 macro P／R 各 ≥80%（IoU≥0.5），另報 mAP、樣本數、延遲 | T05 | `inference/README.md`、`inference/model_manifest.json`：YOLO26s 591 張 test，P 77.79%、R 76.99%、mAP50 82.98%、mAP50–95 63.39% | real-data | 未完成 | 整體 P／R 未達 80%；主要類別 macro 指標與延遲未報告 |
| AC23 | ≥10 位測試者，≥8 位 5 分鐘內從問卷到採用；另報首次設定與例外恢復 | T19 | `docs/qa/T19-scenarios.md`、`docs/qa/T19-timing-sheet.md`（範本）；`docs/qa/T19-status.md`：樣本 0 筆 | — | 未驗證-需真人 | T17 正式環境；招募 10 位測試者 |
| AC24 | 外網 HTTPS；團隊電腦在線支援 YOLO／RAG，離線明確不可用、保留輸入、一般功能可用；連線驗證加密、DB 不對外 | T17 | `test_t17_deploy.py`::test_production_publishes_nothing_and_goes_out_through_the_tunnel、test_team_computer_services_bind_only_to_loopback、test_production_refuses_to_render_without_tunnel_token_or_roots；`test_t17_team_link.py` 4 項；`test_t17_offline.py` 3 項；`docs/T17-deployment.md` 第 9 節清單**全部未勾** | automated-synthetic | 未驗證-需實際環境 | 網域、Cloudflare 兩條 tunnel、Access 服務權杖、外部掃描埠、實機離線／恢復；已知問題 #6（知識管理指令需直連雲端 DB） |
| AC25 | 月費含全部服務 ≤ NT$2,000；並行與重試不能繞過預留；額度不足停止新生成但可查紀錄 | T10、T18 | `test_t10_jobs.py`::test_concurrent_reservations_cannot_exceed_monthly_limit、test_exhausted_budget_stops_new_generation_but_history_still_reads；`test_t18_spend_guard.py`::test_concurrent_users_and_retries_never_exceed_the_cap、test_exhausted_budget_blocks_new_generation_but_history_stays_readable；`test_t18_monthly_cost.py` 12 項；`docs/T17-deployment.md` 第 8 節、`docs/T18-load-and-cost.md` 第 2 節報價表**空白** | automated-synthetic | 部分 | 預留防繞過：通過-自動化；月費：無真實報價（主機、網域、備份位置未選定），`committed_services_twd=1400` 只是佔位；staging 實機防繞過未跑 |
| AC26 | Log 格式、分檔、輪替、清理符合政策；敏感資料不入 Log；執行中與調查檔不被清 | T06、T10 | `test_logging.py`::test_daily_rotation、test_rotation、test_cleanup、test_opened_file_protection、test_sensitive_redaction、test_same_timestamp_rotation_keeps_every_archive、test_budget_exceeded_is_written_to_scheduler_log；`test_ac26_privacy.py`::test_jsonl_privacy、test_uvicorn_access_log_disabled；`test_t10_jobs.py`::test_only_well_formed_tracing_fields_are_logged | automated-synthetic | 通過-自動化 | 正式環境每日 Log 量未實測（spec 要求實測後調整） |
| AC27 | 從獨立備份還原後家庭、已採用餐食、來源／規則快照可讀；保留至少一次實際還原證據 | T17 | `docs/T17-deployment.md` 第 7 節本機演練（2026-10-20，合成資料，同一台 Docker 主機模擬兩個位置）；`test_t17_backup_restore.py` 14 項 | automated-synthetic＋本機演練 | 部分 | 正式環境、真正異地副本的還原演練未做；正式資料量、備份大小、耗時未量測 |
| AC28 | G01–G07 各有可重現證據、版本與結論；未通過項目阻擋相應真實功能交付 | T01–T05、T17、T18、T20 | 本文件下方 Gate 表 | — | 未完成 | G01–G07 無一完整通過；T18、T19 實測未做 |
| AC29 | 只附實際取得、經審核、有來源的知識；錯食材、未審核、偽造引用不顯示；缺知識可省略不影響數值與採用 | T04、T11、T14、T15、T16 | `test_t11_knowledge_store.py`::test_only_approved_current_knowledge_of_final_ingredients_is_returned；`test_t11_knowledge_service.py`::test_citations_must_match_retrieved_reviewed_knowledge、test_records_for_other_ingredients_or_unreviewed_are_rejected_as_invalid；`test_t14_generation.py`::test_only_citations_matching_retrieved_knowledge_are_kept、test_missing_knowledge_is_omitted_without_explanation_call；`test_kn_attribution.py` 3 項；`test_kn_healthnews_crawler.py`::test_pending_crawled_knowledge_is_never_searchable | automated-synthetic（虛構 HTML 範本） | 部分 | G07：授權證據未存檔、沒有任何經人工審核的真實知識；已知問題 #7（食材比對過鬆）、#8（轉載文章可被核准） |
| AC30 | 8/20 比對同家庭 8/19 全餐別已採用菜；未採用、他家庭、他日期不納入；無昨日紀錄正常；別名同菜；指定例外 | T11、T16、T19 | `test_t11_meal_history.py`::test_reads_every_meal_type_of_the_previous_calendar_day_only、test_other_household_and_not_adopted_records_are_ignored、test_no_previous_day_record_does_not_fall_back_to_earlier_dates、test_specified_dish_only_exempts_itself、test_month_boundary_uses_calendar_day；`test_t16_meal_plans.py`::test_adopt_0819_then_0820_generation_excludes_yesterday_except_specified；`test_t15_meal_results.py`::test_previous_day_comparison_and_specified_repeat_are_explained | automated-synthetic | 通過-自動化 | T19 真人複驗；已知問題 #4（結果頁不預先顯示昨日衝突） |
| AC31 | 去重後無資料庫方案自動 AI 且仍排除未指定昨日菜；採用前歷史變動須重驗並拒絕失效結果 | T11、T14、T16 | `test_t14_generation.py`::test_previous_day_exclusion_still_applies_in_ai_mode_except_specified；`test_t11_meal_history.py`::test_history_adopted_after_generation_conflicts、test_specified_exception_survives_recheck；`test_t16_meal_plans.py`::test_history_change_after_generation_that_clashes_rejects_the_stale_result、test_history_change_without_clash_is_adopted_with_the_fresh_comparison_saved | automated-synthetic | 通過-自動化 | 已知問題 #2、#4 會影響體驗（去重後首組營養不符時不回退） |
| AC32 | 知識依最終食材對映，換食材須更新；查無與故障分開；來源提供前只驗證合成流程 | T04、T11、T14、T15 | `test_t11_knowledge_service.py`::test_changed_recipe_ingredients_are_remapped、test_no_knowledge_is_an_omittable_state、test_broken_knowledge_database_is_unavailable_not_zero_rows、test_unreachable_service_is_unavailable；`test_t14_generation.py`::test_knowledge_service_failure_is_reported_separately_and_menu_still_ready；`test_t15_meal_results.py`::test_knowledge_service_failure_is_shown_separately_and_does_not_block；`test_kn_crawl_queue.py` 6 項 | automated-synthetic | 部分 | 真實來源未審核（G07）；已知問題 #3（故障後只能整份重新生成，不能只重試知識說明） |

## 前置驗證 G01–G07

| Gate | 要求摘要 | 責任 ticket | 證據與版本 | 證據類型 | 狀態 | 阻擋項目 | 被阻擋的正式交付 |
|---|---|---|---|---|---|---|---|
| G01 | 食譜來源與授權、版本、去重、結構化用量／步驟、合格數與人工抽查、未達目標差距 | T01 | `docs/T01-recipe-database.md`：iCook 愛料理、工作簿 SHA-256 `6bcaca41549572e39bd0fc40d0d216663df6438ae8be50079f92d81aa32c7304`、293 道／合格 281 道（目標 150）、重複與未合格原因；`config/recipe_sources.json`、`config/recipe_reviews.json`（綁定工作簿雜湊） | real-data＋human（整批確認） | 部分 | 授權文件編號、審核人姓名待補；缺逐道人工抽查紀錄；可調範圍多數 `not_reviewed`（T12 只調整已核准範圍） | 正式結構化食譜庫與條件查詢驗收 |
| G02 | 價格取得方式、商品對映、淨重、地區／促銷／有效期、真實回應與失敗例 | T02 | `docs/T02-pricing.md`、`docs/T02-v2-integration-acceptance.md`：全聯小時達公開頁、policy `pxmart-selection-v2`、真實少量（3 種）成功與跨日快照；`test_t02_pricing.py` | real-data（少量） | 部分 | 432 種食材的完整真實覆蓋率未量測；未選門市／地區；促銷價未解析；授權文件編號待補 | 自動補價、真實包裝與成本驗收 |
| G03 | 成人與未成年一般份量的適用範圍、公式、常數、版本、人工對照 | T03 | `nutrition_target/README.md`：規則 `hpa-dri8-mvp-0.3.0`（雜湊已註冊）、合成人工案例 | automated-synthetic | 未完成 | 來源 PDF 視覺核對與來源檔保存未完成；成人式用於兒少一般餐食的適用性未驗證；文件明言 G03 未通過 | 真實每日基準與當餐目標驗收 |
| G04 | TFDA 樣品／版本、食材／狀態對映、必要欄位、可食部／產率依據、支援範圍、缺值例、人工對照 | T04 | `docs/T04-acceptance-2026-10-16.md`：工作簿 SHA-256 `bd7410e9…5a5ee`、TFDA 2,213 筆（七項完整 878 筆）、忠實匯入差異 0；`docs/T04-T06-merge-pending.md` P01／P03 | real-data（來源忠實匯入）＋automated-synthetic | 未完成 | P01（LLM 烹調係數用途）待決議，目前不使用；沒有代表性真實料理的人工對照；各食材生熟狀態未保存；`匙` 等單位無換算依據 | 真實烹調營養與 ±10% 採用驗收 |
| G05 | 固定模型／知識索引版本與類別測試集、結構化查詢規則、知識相關性與引用核對、品質／費用／延遲 | T05、T11、T14 | 辨識：`inference/model_manifest.json`（AC22 未達標）；結構化查詢：`test_t11_recipe_retrieval.py`（合成）；生成：模型固定 `gpt-5.4-mini-2026-03-17`、向量 `text-embedding-3-small`、`config/model_pricing.json` `plan-2026-10-20`；知識：合成測試 | real-data（YOLO）＋automated-synthetic | 未完成 | YOLO P／R < 80%；真實語料查詢情境未核對；真實模型輸出品質、費用、延遲未量測；真實知識索引不存在，相關性與引用無法核對 | 模型、結構化查詢、知識 RAG 正式驗收 |
| G06 | 五人跨節點實測、兩端記憶體／延遲、離線恢復、完整月費、外網 HTTPS、兩端備份還原 | T17、T18 | `docs/T17-deployment.md`（工具、本機演練）、`docs/T18-load-and-cost.md`（工具與空白表） | automated-synthetic＋本機演練 | 未驗證-需實際環境 | T17 第 9 節清單全部未做；T18 所有量測表空白 | 對外部署驗收 |
| G07 | 營養知識來源、授權／使用條件、食材對映、人工審核、版本、相關性與引用核對、缺知識例 | T04、T11 | 來源已定為健康醫療網（decisions #14）；`docs/knowledge-source-healthnews.md`；爬蟲／審核指令／出處顯示的合成測試 `test_kn_*.py` | automated-synthetic | 未完成 | **書面授權證據未存檔**（文件中存放路徑仍是「待填」）；尚無任何經人工審核並建索引的真實知識；相關性與引用核對案例為 0；已知問題 #6、#7、#8 未修 | 真實營養知識說明與 RAG 驗收 |

## 統計

| 狀態 | AC | Gate |
|---|---|---|
| 通過-自動化 | 17（AC01、03、04、08、10、11、12、14、15、16、17、18、19、20、26、30、31） | 0 |
| 部分 | 9（AC02、05、06、07、13、25、27、29、32） | 2（G01、G02） |
| 未驗證-需實際環境 | 2（AC21、AC24） | 1（G06） |
| 未驗證-需真人 | 1（AC23） | 0 |
| 未完成 | 3（AC09、AC22、AC28） | 4（G03、G04、G05、G07） |
| 合計 | 32 | 7 |

「通過-自動化」的 17 項全部還要經 T19 實際環境複驗，才能算真實功能驗收。

## MVP 完成條件（尚未達成）

1. 已知問題 #1–#8 修正並補測試（#1 直接違反 AC09）。
2. G07 授權證據存檔、完成一批真實知識的人工審核與建索引，並記錄相關性與引用核對案例。
3. T17 正式部署清單全部完成（外網 HTTPS、兩條 tunnel、離線／恢復、正式還原演練）。
4. T18 五人負載與真實報價月費通過（不得放寬門檻或提高上限）。
5. T19 至少 10 位真人測試，≥8 位 5 分鐘內完成。
6. AC22 YOLO 達標，或由使用者核准規格變更。
7. G01–G04 補齊各自缺件（授權編號、審核人、完整價格覆蓋、規則來源核對、烹調依據與人工對照）。
8. 以上證據回填本矩陣，AC28 才能判定。
