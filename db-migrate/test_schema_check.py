#!/usr/bin/env python3
import tempfile
import os
import pytest
from schema_check import parse_schema, check, _extract_columns


class TestParseSchema:
    def test_basic_table(self):
        sql = """
        CREATE TABLE users (
            id SERIAL PRIMARY KEY,
            name VARCHAR(255) NOT NULL,
            email TEXT
        );
        """
        tables = parse_schema(sql)
        assert "users" in tables
        assert tables["users"] == {"id", "name", "email"}

    def test_multiple_tables(self):
        sql = """
        CREATE TABLE games (
            id SERIAL PRIMARY KEY,
            status VARCHAR(20)
        );
        CREATE TABLE players (
            id SERIAL PRIMARY KEY,
            game_id INTEGER
        );
        """
        tables = parse_schema(sql)
        assert len(tables) == 2
        assert "games" in tables
        assert "players" in tables

    def test_constraint_keywords_excluded(self):
        sql = """
        CREATE TABLE orders (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL,
            CONSTRAINT fk_user FOREIGN KEY (user_id) REFERENCES users(id),
            UNIQUE (user_id)
        );
        """
        tables = parse_schema(sql)
        assert "id" in tables["orders"]
        assert "user_id" in tables["orders"]
        assert len(tables["orders"]) == 2

    def test_check_constraint_with_commas(self):
        sql = """
        CREATE TABLE player_factions (
            id SERIAL PRIMARY KEY,
            faction VARCHAR(20) NOT NULL CHECK (faction IN ('SHE', 'Tenki', 'Sugar', 'Tuners'))
        );
        """
        tables = parse_schema(sql)
        assert "id" in tables["player_factions"]
        assert "faction" in tables["player_factions"]

    def test_empty_schema(self):
        tables = parse_schema("")
        assert tables == {}

    def test_case_insensitive(self):
        sql = """
        create table Users (
            ID serial primary key,
            Name varchar(255)
        );
        """
        tables = parse_schema(sql)
        assert "users" in tables
        assert "id" in tables["users"]
        assert "name" in tables["users"]


class TestCheck:
    def _write_temp(self, content: str) -> str:
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".sql", delete=False)
        f.write(content)
        f.close()
        return f.name

    def test_no_changes(self):
        sql = "CREATE TABLE users (id SERIAL PRIMARY KEY, name TEXT);"
        old = self._write_temp(sql)
        new = self._write_temp(sql)
        try:
            warnings = check(old, new)
            assert warnings == []
        finally:
            os.unlink(old)
            os.unlink(new)

    def test_add_column(self):
        old_sql = "CREATE TABLE users (id SERIAL PRIMARY KEY);"
        new_sql = "CREATE TABLE users (id SERIAL PRIMARY KEY, name TEXT);"
        old = self._write_temp(old_sql)
        new = self._write_temp(new_sql)
        try:
            warnings = check(old, new)
            assert warnings == []
        finally:
            os.unlink(old)
            os.unlink(new)

    def test_drop_column(self):
        old_sql = "CREATE TABLE users (id SERIAL PRIMARY KEY, name TEXT, email TEXT);"
        new_sql = "CREATE TABLE users (id SERIAL PRIMARY KEY, name TEXT);"
        old = self._write_temp(old_sql)
        new = self._write_temp(new_sql)
        try:
            warnings = check(old, new)
            assert len(warnings) == 1
            assert "DROP COLUMN: users.email" in warnings[0]
        finally:
            os.unlink(old)
            os.unlink(new)

    def test_drop_table(self):
        old_sql = """
        CREATE TABLE users (id SERIAL PRIMARY KEY);
        CREATE TABLE logs (id SERIAL PRIMARY KEY);
        """
        new_sql = "CREATE TABLE users (id SERIAL PRIMARY KEY);"
        old = self._write_temp(old_sql)
        new = self._write_temp(new_sql)
        try:
            warnings = check(old, new)
            assert len(warnings) == 1
            assert "DROP TABLE: logs" in warnings[0]
        finally:
            os.unlink(old)
            os.unlink(new)

    def test_empty_old_schema(self):
        old_sql = ""
        new_sql = "CREATE TABLE users (id SERIAL PRIMARY KEY, name TEXT);"
        old = self._write_temp(old_sql)
        new = self._write_temp(new_sql)
        try:
            warnings = check(old, new)
            assert warnings == []
        finally:
            os.unlink(old)
            os.unlink(new)

    def test_add_table(self):
        old_sql = "CREATE TABLE users (id SERIAL PRIMARY KEY);"
        new_sql = """
        CREATE TABLE users (id SERIAL PRIMARY KEY);
        CREATE TABLE logs (id SERIAL PRIMARY KEY, message TEXT);
        """
        old = self._write_temp(old_sql)
        new = self._write_temp(new_sql)
        try:
            warnings = check(old, new)
            assert warnings == []
        finally:
            os.unlink(old)
            os.unlink(new)
