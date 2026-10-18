"""Knowledge contract tests (R12, G07; AC29, AC32). All records are synthetic, not real sources."""
from pathlib import Path
import tempfile
import unittest

from knowledge_service import KnowledgeStore, retrieve_knowledge, verify_citations


def record(knowledge_id='test-only', ingredient_id='A', version='1'):
    return {'knowledge_id': knowledge_id, 'ingredient_id': ingredient_id, 'excerpt': 'Synthetic quotation',
            'source_url': 'https://example.invalid/source', 'source_version': version,
            'rights': 'Synthetic test data', 'conditions': 'test only'}


def approve(store, knowledge_id='test-only', version='1', approved=True):
    return store.review(knowledge_id, version, reviewer='TEST', reviewed_at='2026-01-01',
                        approved=approved, note='Synthetic citation checked')


class KnowledgeStoreTests(unittest.TestCase):
    def test_only_approved_knowledge_is_retrieved(self):
        store = KnowledgeStore(['A', 'B'])
        store.import_record(record())
        self.assertEqual(store.retrieve(['A'])['status'], 'not_found')
        approve(store)
        snapshot = store.retrieve(['A'])
        self.assertEqual(snapshot['status'], 'available')
        self.assertEqual([r['knowledge_id'] for r in snapshot['records']], ['test-only'])

    def test_rejected_review_is_not_retrieved_and_review_is_immutable(self):
        store = KnowledgeStore(['A'])
        store.import_record(record())
        approve(store, approved=False)
        self.assertEqual(store.retrieve(['A'])['status'], 'not_found')
        with self.assertRaises(ValueError):
            approve(store)

    def test_review_requires_reviewer_date_and_note(self):
        store = KnowledgeStore(['A'])
        store.import_record(record())
        with self.assertRaises(ValueError):
            store.review('test-only', '1', reviewer='', reviewed_at='2026-01-01', approved=True, note='x')
        with self.assertRaises(ValueError):
            store.review('test-only', '1', reviewer='TEST', reviewed_at='2026-01-01', approved='yes', note='x')

    def test_import_rejects_unknown_ingredient_bad_url_and_missing_fields(self):
        store = KnowledgeStore(['A'])
        for bad in ({**record(), 'ingredient_id': 'Z'}, {**record(), 'source_url': 'made-up'},
                    {**record(), 'rights': ' '}, {k: v for k, v in record().items() if k != 'excerpt'}):
            with self.assertRaises(ValueError):
                store.import_record(bad)

    def test_new_version_supersedes_old_until_reviewed_and_keeps_old_snapshot(self):
        store = KnowledgeStore(['A'])
        store.import_record(record())
        approve(store)
        snapshot = store.retrieve(['A'])
        store.import_record(record(version='2'))
        self.assertEqual(store.retrieve(['A'])['status'], 'not_found')
        self.assertEqual(snapshot['records'][0]['source_version'], '1')
        approve(store, version='2')
        self.assertEqual(store.retrieve(['A'])['records'][0]['source_version'], '2')
        with self.assertRaises(ValueError):
            store.import_record(record(version='2'))

    def test_returned_snapshot_is_independent_of_store(self):
        store = KnowledgeStore(['A'])
        store.import_record(record())
        approve(store)
        snapshot = store.retrieve(['A'])
        snapshot['records'][0]['excerpt'] = 'tampered'
        self.assertEqual(store.retrieve(['A'])['records'][0]['excerpt'], 'Synthetic quotation')

    def test_by_ingredient_marks_missing_knowledge_and_follows_recipe_changes(self):
        store = KnowledgeStore(['A', 'B', 'C'])
        store.import_record(record('k-a', 'A'))
        store.import_record(record('k-b', 'B'))
        approve(store, 'k-a')
        approve(store, 'k-b')
        before = store.retrieve(['A', 'C'])
        self.assertEqual(before['by_ingredient'], {'A': 'available', 'C': 'not_found'})
        # Recipe changed: A removed, B added. Old A knowledge must not remain.
        after = store.retrieve(['B', 'C'])
        self.assertEqual({r['ingredient_id'] for r in after['records']}, {'B'})
        self.assertEqual(after['by_ingredient'], {'B': 'available', 'C': 'not_found'})

    def test_final_ingredient_ids_are_validated(self):
        store = KnowledgeStore(['A'])
        for bad in ('A', [''], [None]):
            with self.assertRaises(ValueError):
                store.retrieve(bad)


class RetrieveKnowledgeTests(unittest.TestCase):
    def test_save_load_round_trip(self):
        store = KnowledgeStore(['A'])
        store.import_record(record())
        approve(store)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'knowledge.json'
            store.save(path)
            result = retrieve_knowledge(path, ['A'], ['A'])
        self.assertEqual(result['status'], 'available')
        self.assertEqual(result['records'][0]['reviewer'], 'TEST')

    def test_missing_or_corrupt_store_is_unavailable_not_not_found(self):
        with tempfile.TemporaryDirectory() as folder:
            missing = retrieve_knowledge(Path(folder) / 'absent.json', ['A'], ['A'])
            corrupt_path = Path(folder) / 'corrupt.json'
            corrupt_path.write_text('{not json', encoding='utf-8')
            corrupt = retrieve_knowledge(corrupt_path, ['A'], ['A'])
            invalid_path = Path(folder) / 'invalid.json'
            invalid_path.write_text('[{"knowledge_id": "x"}]', encoding='utf-8')
            invalid = retrieve_knowledge(invalid_path, ['A'], ['A'])
        for result in (missing, corrupt, invalid):
            self.assertEqual(result['status'], 'unavailable')
            self.assertEqual(result['records'], [])

    def test_empty_store_is_not_found(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'empty.json'
            path.write_text('[]', encoding='utf-8')
            self.assertEqual(retrieve_knowledge(path, ['A'], ['A'])['status'], 'not_found')


class CitationTests(unittest.TestCase):
    def setUp(self):
        self.store = KnowledgeStore(['A', 'B'])
        self.store.import_record(record('k-a', 'A'))
        approve(self.store, 'k-a')
        self.store.import_record(record('k-b', 'B'))  # pending, never approved
        self.snapshot = self.store.retrieve(['A', 'B'])

    def test_citation_of_retrieved_knowledge_is_valid(self):
        result = verify_citations([{'knowledge_id': 'k-a', 'source_version': '1', 'ingredient_id': 'A'}],
                                  self.snapshot)
        self.assertTrue(result['valid'])
        self.assertEqual(len(result['citations']), 1)

    def test_invented_wrong_ingredient_and_unreviewed_citations_are_invalid(self):
        bad = [{'knowledge_id': 'invented', 'source_version': '1', 'ingredient_id': 'A'},
               {'knowledge_id': 'k-a', 'source_version': '1', 'ingredient_id': 'B'},
               {'knowledge_id': 'k-a', 'source_version': '9', 'ingredient_id': 'A'},
               {'knowledge_id': 'k-b', 'source_version': '1', 'ingredient_id': 'B'},
               'k-a']
        result = verify_citations(bad, self.snapshot)
        self.assertFalse(result['valid'])
        self.assertEqual(len(result['invalid']), len(bad))
        self.assertEqual(result['citations'], [])

    def test_nothing_is_valid_against_unavailable_snapshot(self):
        result = verify_citations([{'knowledge_id': 'k-a', 'source_version': '1', 'ingredient_id': 'A'}],
                                  {'status': 'unavailable', 'records': []})
        self.assertFalse(result['valid'])


if __name__ == '__main__':
    unittest.main()
