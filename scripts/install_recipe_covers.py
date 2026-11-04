"""Import the 293 human-reviewed covers for the active 1103 V3 release."""
import argparse
import csv
import hashlib
import io
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit

from alembic import command
from alembic.config import Config
from PIL import Image
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.core.config import settings
from app.models.recipe import Recipe, RecipeCover, RecipeDataRelease
from t04_db_support import guard

RELEASE = "recipe-1103-v3-dd4f75c13772"
HEAD = "d1104covers1"
MEDIA_TYPES = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}


def load_covers(folder):
    covers = {}
    with (folder / "image_manifest.csv").open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            recipe_id = row["recipe_id"]
            if recipe_id in covers or not re.fullmatch(r"R[0-9]{4}", recipe_id):
                raise ValueError(f"Invalid or duplicate recipe ID: {recipe_id}")
            filename = row["filename"]
            if not re.fullmatch(rf"{recipe_id}_pic_01\.(jpg|png|webp)", filename):
                raise ValueError(f"Invalid cover filename: {filename}")
            image_url = urlsplit(row["image_url"])
            if image_url.scheme != "https" or image_url.hostname != "imgproxy.icook.network":
                raise ValueError(f"Unexpected image origin: {recipe_id}")
            data = (folder / filename).read_bytes()
            digest = hashlib.sha256(data).hexdigest()
            if digest != row["sha256"] or len(data) != int(row["bytes"]):
                raise ValueError(f"File mismatch: {recipe_id}")
            with Image.open(io.BytesIO(data)) as image:
                media_type = MEDIA_TYPES[image.format]
                if image.size != (int(row["width"]), int(row["height"])):
                    raise ValueError(f"Image dimensions mismatch: {recipe_id}")
                image.verify()
            covers[recipe_id] = (row["recipe_name"], row["source_url"], {
                "recipe_id": recipe_id, "recipe_release_id": RELEASE,
                "filename": filename, "image_url": row["image_url"], "sha256": digest,
                "media_type": media_type, "image_bytes": data,
            })
    if len(covers) != 293:
        raise ValueError(f"Expected 293 covers, got {len(covers)}")
    return covers


def verify_binding(db, covers):
    release = db.get(RecipeDataRelease, RELEASE)
    if not release or not release.active or release.source_sha256 != "dd4f75c1377299fdef858c98d8625cfbdee1e21dd7699f3f78e210048cf0289b":
        raise ValueError("Active recipe release identity mismatch")
    recipes = {row.recipe_id: (row.recipe_name, row.source_url) for row in db.execute(
        select(Recipe.recipe_id, Recipe.recipe_name, Recipe.source_url).where(Recipe.recipe_release_id == RELEASE)
    )}
    if recipes != {key: value[:2] for key, value in covers.items()}:
        mismatches = sorted(key for key in recipes.keys() | covers.keys()
                            if recipes.get(key) != (covers[key][:2] if key in covers else None))
        raise ValueError(f"Recipe ID, name or source mismatch: {mismatches}")


def install(folder):
    covers = load_covers(folder)
    engine = create_engine(settings.database_url)
    guard(engine)
    with Session(engine) as db:
        verify_binding(db, covers)
        head = db.scalar(text("SELECT version_num FROM alembic_version"))
    if head not in ("d1103d04b001", HEAD):
        raise ValueError(f"Unexpected V3 schema revision: {head}")
    if head != HEAD:
        config = Config(str(ROOT / "alembic.ini"))
        config.attributes["database_url"] = settings.database_url
        command.upgrade(config, HEAD)
    with Session(engine) as db, db.begin():
        db.execute(text("LOCK TABLE recipes, recipe_data_releases IN SHARE MODE"))
        db.execute(text("LOCK TABLE recipe_covers IN SHARE ROW EXCLUSIVE MODE"))
        verify_binding(db, covers)
        existing = {row.recipe_id: row for row in db.scalars(
            select(RecipeCover).where(RecipeCover.recipe_release_id == RELEASE)
        )}
        inserted = 0
        for recipe_id, (_, _, values) in covers.items():
            current = existing.get(recipe_id)
            if current is None:
                db.add(RecipeCover(**values))
                inserted += 1
            elif any(getattr(current, key) != value for key, value in values.items()):
                raise ValueError(f"Existing cover differs: {recipe_id}")
        db.flush()
        stored = db.scalars(select(RecipeCover).where(RecipeCover.recipe_release_id == RELEASE)).all()
        if {row.recipe_id: hashlib.sha256(row.image_bytes).hexdigest() for row in stored} != {
            recipe_id: values["sha256"] for recipe_id, (_, _, values) in covers.items()
        }:
            raise ValueError("Stored cover coverage or content mismatch")
    print(f"{RELEASE}: {len(stored)} verified covers, {inserted} inserted, {len(stored)-inserted} already present")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reviewed_cover_folder", type=Path)
    install(parser.parse_args().reviewed_cover_folder)
