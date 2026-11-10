"""Immutable T01 revisions layered over their original T01/FOOD release."""
from dataclasses import dataclass
from decimal import Decimal
from hashlib import sha256

import sqlalchemy as sa

from app.core.config import settings
from app.management.contracts import digest
from app.management.models import tables as T
from app.models import Recipe, RecipeDataRelease

CHANNEL = 'recipe_catalog'
BASELINE = 't01:baseline'
RECIPE_PREFIX = 't01v:'


class RecipeCatalogUnavailable(sa.exc.SQLAlchemyError):
    pass


@dataclass(frozen=True)
class ManagedRecipe:
    logical_id: str
    version_id: str
    content_hash: str
    content: dict
    base_release_id: str
    baseline_dish_id: str | None

    @property
    def id(self):
        return RECIPE_PREFIX + self.version_id

    @property
    def steps(self):
        return sorted(self.content['steps'], key=lambda item: item['step_no'])

    @property
    def methods(self):
        from app.services.recipe_import import _method_candidates
        instructions = [step['instruction'].replace('拌炒', '炒') for step in self.steps]
        methods = _method_candidates(instructions)
        if '氣炸' in methods and not any('炸' in step.replace('氣炸', '') for step in instructions):
            methods.remove('炸')
        # Mixing is preparation when the same recipe also names a heating method.
        return [method for method in methods if method != '拌'] if len(methods) > 1 else methods

    @property
    def servings(self):
        servings = self.content['servings']
        count = servings['count']
        if count is None:
            return None
        if servings['basis'] == 'portions':
            return Decimal(count)
        per_batch = servings['portions_per_batch']
        return Decimal(count) * Decimal(per_batch) if per_batch else None

    def ingredient_rows(self):
        for sequence, line in enumerate(self.content['ingredients'], 1):
            weight = line['weight_g']['value']
            amount = line['amount']['value']
            yield dict(
                recipe_id=self.id, recipe_ingredient_id=sha256(f'{self.id}:{sequence}'.encode()).hexdigest()[:32],
                sequence=sequence, food_id=line['food_id'], canonical_name=line['canonical_name'],
                source_weight_g=None, weight_g=Decimal(weight) if weight is not None else None,
                raw_text=line['raw_text'], unit=line['unit'], amount=Decimal(amount) if amount is not None else None,
            )

    def summary(self):
        from app.services.recipe_import import standard_dish_id
        content = self.content
        return dict(recipe_id=self.id, recipe_name=content['name'], category=content['category'],
                    cuisine=content['cuisine'], meal_type=content['meal_type'], aliases=content['aliases'],
                    standard_dish_id=(content['standard_dish_id'] or self.baseline_dish_id
                                      or standard_dish_id(self.logical_id)))

    def evidence(self, publication_id):
        return dict(recipe_id=self.id, recipe_name=self.content['name'], source_name='管理審核食譜',
                    source_url=None, review_status='approved', original_servings=self.content['servings']['count'],
                    servings_basis=self.content['servings']['basis'], content_hash=self.content_hash,
                    recipe_publication_id=publication_id, recipe_version_id=self.version_id,
                    logical_recipe_id=self.logical_id, artifact_ids=[ref['artifact_id'] for ref in self.content['sources']])


@dataclass(frozen=True)
class Catalog:
    publication_id: str
    base_release_id: str
    recipes: dict[str, ManagedRecipe]
    legacy_ids: frozenset[str]


def publication_recipes(db, publication):
    manifest = publication['manifest']
    if digest('manifest', manifest) != publication['manifest_hash']:
        raise RecipeCatalogUnavailable('Invalid recipe publication manifest')
    pins = manifest['dependency_pins']
    release = pins['legacy_recipe_release_id']
    members, legacy_ids = {}, set()
    for member in manifest['members']:
        ref, logical_id = member['ref'], member['logical_id']
        if ref['kind'] == 'managed':
            members[logical_id] = ref
        elif (ref['legacy_recipe_release_id'] == release
              and ref['captured_content_hash'] == pins['legacy_db_capture_sha256']):
            legacy_ids.add(logical_id)
        else:
            raise RecipeCatalogUnavailable('Invalid legacy recipe reference')
    versions = T['entity_versions']
    rows = {row['logical_id']: row for row in db.execute(sa.select(versions).where(
        versions.c.domain == 'recipe', versions.c.id.in_([ref['version_id'] for ref in members.values()])
    )).mappings()}
    baseline = dict(db.execute(sa.select(Recipe.recipe_id, Recipe.standard_dish_id).where(
        Recipe.recipe_release_id == release, Recipe.recipe_id.in_(members),
    )).all())
    recipes = {}
    for logical_id, ref in members.items():
        row = rows.get(logical_id)
        if (row is None or row['id'] != ref['version_id'] or row['content_hash'] != ref['content_hash']
                or digest('content', {'domain': 'recipe', 'content': row['content']}) != ref['content_hash']):
            raise RecipeCatalogUnavailable('Invalid managed recipe version')
        recipe = ManagedRecipe(logical_id, row['id'], row['content_hash'], row['content'], release, baseline.get(logical_id))
        recipes[recipe.id] = recipe
    return Catalog(publication['id'], release, recipes, frozenset(legacy_ids))


def active_catalog(db):
    if not settings.management_recipe_catalog_enabled or db.get_bind().dialect.name != 'postgresql':
        return None
    cached = db.info.get(CHANNEL)
    if cached and cached[0] is db.get_transaction():
        return cached[1]
    bindings, publications = T['active_bindings'], T['publications']
    row = db.execute(sa.select(bindings.c.publication_id.label('bound_id'), publications).outerjoin(
        publications, publications.c.id == bindings.c.publication_id
    ).where(bindings.c.channel == CHANNEL)).mappings().one_or_none()
    if row is None:
        raise RecipeCatalogUnavailable('Recipe binding is missing')
    catalog = None
    if row['bound_id'] is not None:
        if row['domain'] != 'recipe':
            raise RecipeCatalogUnavailable('Recipe binding points to another domain')
        catalog = publication_recipes(db, row)
        pins = row['manifest']['dependency_pins']
        source = db.scalar(sa.select(RecipeDataRelease.source_sha256).where(
            RecipeDataRelease.active.is_(True), RecipeDataRelease.release_id == catalog.base_release_id))
        if not source or source != pins['legacy_recipe_source_sha256'] or (
            settings.generation_recipe_release_id not in (None, '', catalog.base_release_id)
        ):
            raise RecipeCatalogUnavailable('Active recipe publication does not match the T01 baseline')
    db.info[CHANNEL] = (db.get_transaction(), catalog)
    return catalog


def managed_recipe(db, recipe_id):
    if not recipe_id.startswith(RECIPE_PREFIX):
        return None
    catalog = active_catalog(db)
    recipe = catalog.recipes.get(recipe_id) if catalog else None
    if recipe is None:
        raise LookupError(recipe_id)
    return recipe


def planning_rows(db, catalog, recipe_ids):
    """Build trusted T12 input from the same active snapshot used by T11."""
    from nutrition_planning.integration import RecipeRows, load_database_recipe_rows

    legacy_ids = [rid for rid in recipe_ids if rid not in catalog.recipes]
    legacy = load_database_recipe_rows(db.connection(), legacy_ids) if legacy_ids else RecipeRows((), (), ())
    recipes, ingredients, steps = list(legacy.recipes), list(legacy.ingredients), list(legacy.steps)
    for rid in recipe_ids:
        recipe = catalog.recipes.get(rid)
        if recipe is None:
            continue
        recipes.append(dict(recipe.summary(), recipe_release_id=catalog.base_release_id,
                            original_servings=recipe.servings, cooking_methods=recipe.methods))
        ingredients.extend(recipe.ingredient_rows())
        steps.extend(dict(recipe_id=rid, step_no=step['step_no'], instruction_clean=step['instruction'])
                     for step in recipe.steps)
    return RecipeRows(tuple(recipe_ids), tuple(recipes), tuple(ingredients), tuple(steps))


def receipt_line_matches(db, line, release_id, weight):
    """Verify a pinned managed line even if the active binding changed mid-job."""
    if not line.dish_id or not line.dish_id.startswith(RECIPE_PREFIX):
        return False
    versions, members, publications = T['entity_versions'], T['publication_members'], T['publications']
    row = db.execute(sa.select(versions, publications.c.manifest).select_from(versions).join(
        members, members.c.version_id == versions.c.id,
    ).join(publications, publications.c.id == members.c.publication_id).where(
        publications.c.id == line.recipe_publication_id, publications.c.domain == 'recipe',
        versions.c.id == line.dish_id.removeprefix(RECIPE_PREFIX),
    )).mappings().one_or_none()
    if not row or row['manifest']['dependency_pins']['legacy_recipe_release_id'] != release_id:
        return False
    recipe = ManagedRecipe(row['logical_id'], row['id'], row['content_hash'], row['content'], release_id, None)
    if not recipe.servings or recipe.methods != [line.cooking_method]:
        return False
    return any(item['recipe_ingredient_id'] == line.recipe_ingredient_id and item['food_id'] == line.food_id
               and item['weight_g'] is not None and item['weight_g'] / recipe.servings == weight
               for item in recipe.ingredient_rows())
