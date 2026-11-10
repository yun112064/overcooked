\set ON_ERROR_STOP on
BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;

SELECT json_build_object(
    'database', current_database(),
    'read_only', current_setting('transaction_read_only'),
    'release', (SELECT release_id FROM recipe_data_releases WHERE active),
    'recipe_count', (SELECT count(*) FROM recipes r
        JOIN recipe_data_releases v ON v.release_id = r.recipe_release_id WHERE v.active),
    'step_count', (SELECT count(*) FROM recipe_steps s
        JOIN recipe_data_releases v ON v.release_id = s.recipe_release_id WHERE v.active)
);

SELECT json_build_object(
    'recipe_id', r.recipe_id, 'recipe_name', r.recipe_name,
    'step_id', s.step_id, 'step_no', s.step_no, 'text', s.instruction_clean
)
FROM recipe_steps s
JOIN recipes r ON r.recipe_id = s.recipe_id AND r.recipe_release_id = s.recipe_release_id
JOIN recipe_data_releases v ON v.release_id = s.recipe_release_id AND v.active
ORDER BY r.recipe_id, s.step_no, s.step_id;

ROLLBACK;
