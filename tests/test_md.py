"""Unit tests for wiki_spaces._md."""

from __future__ import annotations

from wiki_spaces import _md


# ---------- parse_section_entries / has_section ----------

SAMPLE = """# Hello

## What this space is

Some prose.

## Spaces

- [foo/](foo/index.md) — foo space
- [bar/](bar/index.md)
- [[wiki-style]] — wikilink style

## Items

- [readme.md](readme.md) — readme
"""


def test_has_section_present():
    assert _md.has_section(SAMPLE, "Spaces") is True
    assert _md.has_section(SAMPLE, "Items") is True


def test_has_section_absent():
    assert _md.has_section(SAMPLE, "Missing") is False


def test_parse_section_entries_markdown_links():
    entries = _md.parse_section_entries(SAMPLE, "Spaces")
    assert entries[0].label == "foo/"
    assert entries[0].href == "foo/index.md"
    assert entries[0].description == "foo space"
    assert entries[0].wikilink is None


def test_parse_section_entries_no_description():
    entries = _md.parse_section_entries(SAMPLE, "Spaces")
    assert entries[1].description is None


def test_parse_section_entries_wikilink_form():
    entries = _md.parse_section_entries(SAMPLE, "Spaces")
    assert entries[2].wikilink == "wiki-style"
    assert entries[2].href is None
    assert entries[2].description == "wikilink style"


def test_parse_section_entries_section_absent():
    assert _md.parse_section_entries(SAMPLE, "Missing") == []


def test_parse_section_stops_at_next_heading():
    text = "## Spaces\n\n- [a/](a/index.md)\n\n## Items\n\n- [b.md](b.md)\n"
    spaces = _md.parse_section_entries(text, "Spaces")
    items = _md.parse_section_entries(text, "Items")
    assert len(spaces) == 1
    assert len(items) == 1
    assert spaces[0].href == "a/index.md"
    assert items[0].href == "b.md"


# ---------- add_entry / remove_entry ----------

def test_add_entry_to_existing_section():
    text = "## Spaces\n\n- [foo/](foo/index.md)\n"
    out = _md.add_entry(text, "Spaces", "bar/", "bar/index.md", "bar space")
    entries = _md.parse_section_entries(out, "Spaces")
    assert len(entries) == 2
    assert entries[1].href == "bar/index.md"
    assert entries[1].description == "bar space"


def test_add_entry_is_idempotent_on_href():
    text = "## Spaces\n\n- [foo/](foo/index.md) — original\n"
    once = _md.add_entry(text, "Spaces", "foo/", "foo/index.md", "duplicate")
    twice = _md.add_entry(once, "Spaces", "foo/", "foo/index.md", "duplicate")
    assert once == text
    assert twice == text


def test_add_entry_creates_missing_section():
    text = "# Hello\n"
    out = _md.add_entry(text, "Spaces", "foo/", "foo/index.md", "first")
    assert "## Spaces" in out
    entries = _md.parse_section_entries(out, "Spaces")
    assert entries[0].href == "foo/index.md"


def test_add_entry_preserves_trailing_newline():
    text = "## Spaces\n\n- [foo/](foo/index.md)\n"
    out = _md.add_entry(text, "Spaces", "bar/", "bar/index.md")
    assert out.endswith("\n")


def test_remove_entry_strips_matching_href():
    text = "## Spaces\n\n- [foo/](foo/index.md)\n- [bar/](bar/index.md)\n"
    out = _md.remove_entry(text, "Spaces", "foo/index.md")
    entries = _md.parse_section_entries(out, "Spaces")
    assert len(entries) == 1
    assert entries[0].href == "bar/index.md"


def test_remove_entry_no_match_is_noop():
    text = "## Spaces\n\n- [foo/](foo/index.md)\n"
    out = _md.remove_entry(text, "Spaces", "missing/index.md")
    assert out == text


def test_remove_entry_section_absent_is_noop():
    text = "# Hello\n"
    out = _md.remove_entry(text, "Spaces", "foo/index.md")
    assert out == text


# ---------- find_wikilinks / resolve_wikilink ----------

def test_find_wikilinks_strips_alias_and_heading():
    text = "See [[foo|alias]], [[bar#heading]], [[baz]] and inline [[qux|q]]."
    assert _md.find_wikilinks(text) == ["foo", "bar", "baz", "qux"]


def test_find_wikilinks_empty():
    assert _md.find_wikilinks("plain text, no links") == []


def test_resolve_wikilink_by_filename(tmp_path):
    page = tmp_path / "concepts" / "foo.md"
    page.parent.mkdir()
    page.write_text("")
    other = tmp_path / "skills" / "other.md"
    other.parent.mkdir()
    other.write_text("")
    candidates = {page.resolve(), other.resolve()}
    found = _md.resolve_wikilink("foo", tmp_path / "elsewhere", candidates)
    assert found == page.resolve()


def test_resolve_wikilink_with_md_suffix(tmp_path):
    page = tmp_path / "foo.md"
    page.write_text("")
    candidates = {page.resolve()}
    assert _md.resolve_wikilink("foo.md", tmp_path, candidates) == page.resolve()


def test_resolve_wikilink_unknown(tmp_path):
    assert _md.resolve_wikilink("missing", tmp_path, set()) is None


def test_resolve_wikilink_prefers_base_relative(tmp_path):
    """A page in the linking page's own directory wins over a same-named
    page elsewhere."""
    here = tmp_path / "projects" / "a" / "index.md"
    here.parent.mkdir(parents=True)
    here.write_text("")
    elsewhere = tmp_path / "projects" / "b" / "index.md"
    elsewhere.parent.mkdir(parents=True)
    elsewhere.write_text("")
    candidates = {here.resolve(), elsewhere.resolve()}
    base = tmp_path / "projects" / "a"
    assert _md.resolve_wikilink("index", base, candidates) == here.resolve()


def test_resolve_wikilink_duplicate_filenames_deterministic(tmp_path):
    """Duplicate filenames across spaces resolve deterministically to the
    candidate closest to the linking page's directory."""
    near = tmp_path / "projects" / "a" / "notes" / "setup.md"
    near.parent.mkdir(parents=True)
    near.write_text("")
    far = tmp_path / "archive" / "old" / "deep" / "setup.md"
    far.parent.mkdir(parents=True)
    far.write_text("")
    candidates = {near.resolve(), far.resolve()}
    base = tmp_path / "projects" / "a"
    for _ in range(5):
        assert _md.resolve_wikilink("setup", base, candidates) == near.resolve()


def test_path_distance():
    from pathlib import Path

    a = Path("/w/projects/a")
    assert _md._path_distance(a, a) == 0
    assert _md._path_distance(a, Path("/w/projects/a/notes")) == 1
    assert _md._path_distance(a, Path("/w/projects/b")) == 2


# ---------- frontmatter ----------

FM_SAMPLE = """---
title: >-
  A Long Title
  Across Lines
tags: [foo, bar]
summary: short summary
aliases: []
---

body text here
"""


def test_split_frontmatter_present():
    fm, body = _md.split_frontmatter(FM_SAMPLE)
    assert fm is not None
    assert "title:" in fm
    assert body.startswith("\nbody text here")


def test_split_frontmatter_absent():
    fm, body = _md.split_frontmatter("# No frontmatter\nbody")
    assert fm is None
    assert body == "# No frontmatter\nbody"


def test_parse_frontmatter_scalar_and_array():
    parsed = _md.parse_frontmatter(FM_SAMPLE)
    assert parsed is not None
    assert parsed["summary"] == "short summary"
    assert parsed["tags"] == ["foo", "bar"]
    assert parsed["aliases"] == []


def test_parse_frontmatter_folded_scalar_joins_lines():
    parsed = _md.parse_frontmatter(FM_SAMPLE)
    assert parsed is not None
    assert parsed["title"] == "A Long Title Across Lines"


def test_parse_frontmatter_absent():
    assert _md.parse_frontmatter("no frontmatter here") is None


def test_parse_frontmatter_block_list():
    fm = (
        "---\n"
        "title: Page\n"
        "tags:\n"
        "  - python\n"
        "  - typing\n"
        "aliases:\n"
        "  - 'quoted alias'\n"
        "  - \"double quoted\"\n"
        "summary: short\n"
        "---\n"
        "body\n"
    )
    parsed = _md.parse_frontmatter(fm)
    assert parsed is not None
    assert parsed["tags"] == ["python", "typing"]
    assert parsed["aliases"] == ["quoted alias", "double quoted"]
    assert parsed["summary"] == "short"
    assert parsed["title"] == "Page"


def test_parse_frontmatter_block_list_followed_by_scalar():
    fm = (
        "---\n"
        "tags:\n"
        "  - foo\n"
        "category: concept\n"
        "---\n"
        "body\n"
    )
    parsed = _md.parse_frontmatter(fm)
    assert parsed is not None
    assert parsed["tags"] == ["foo"]
    assert parsed["category"] == "concept"


def test_parse_frontmatter_empty_key_stays_empty_string():
    fm = "---\naliases:\ntags: [x]\n---\nbody\n"
    parsed = _md.parse_frontmatter(fm)
    assert parsed is not None
    assert parsed["aliases"] == ""
    assert parsed["tags"] == ["x"]


def test_parse_frontmatter_block_list_preserves_quoted_commas():
    """Block-list items keep their full post-dash content; commas inside a
    quoted item must not split it (kepano emits this for multi-word aliases)."""
    fm = (
        "---\n"
        "tags:\n"
        "  - \"foo, bar\"\n"
        "  - baz\n"
        "---\n"
        "body\n"
    )
    parsed = _md.parse_frontmatter(fm)
    assert parsed is not None
    assert parsed["tags"] == ["foo, bar", "baz"]


def test_update_frontmatter_replaces_existing_key():
    out = _md.update_frontmatter_field(FM_SAMPLE, "summary", "new summary")
    assert _md.parse_frontmatter(out)["summary"] == "new summary"


def test_update_frontmatter_appends_missing_key():
    out = _md.update_frontmatter_field(FM_SAMPLE, "category", "concept")
    assert _md.parse_frontmatter(out)["category"] == "concept"


def test_update_frontmatter_noop_when_absent():
    text = "# No frontmatter\n"
    assert _md.update_frontmatter_field(text, "summary", "x") == text


# ---------- strip_code_spans ----------

def test_strip_code_spans_blanks_fenced_block():
    out = _md.strip_code_spans("before\n```\n[[fake]]\n```\nafter")
    assert "[[fake]]" not in out
    assert "before" in out and "after" in out


def test_strip_code_spans_blanks_inline_code():
    out = _md.strip_code_spans("see `[[inline]]` here")
    assert "[[inline]]" not in out
    assert "see" in out and "here" in out


def test_strip_code_spans_keeps_real_links():
    assert "[[real-link]]" in _md.strip_code_spans("this [[real-link]] stays put")


def test_strip_code_spans_tilde_fence():
    out = _md.strip_code_spans("p\n~~~\n[[t]]\n~~~\nq")
    assert "[[t]]" not in out
    assert "p" in out and "q" in out


def test_strip_code_spans_preserves_line_count():
    text = "a\n```\nx\ny\n```\nb"
    assert len(_md.strip_code_spans(text).splitlines()) == len(text.splitlines())


def test_strip_code_spans_fence_closes_on_same_char():
    # A ~~~ line inside a ``` block does not close it; the matching ``` does.
    out = _md.strip_code_spans("```\n~~~\n[[still-code]]\n```\n[[live]]")
    assert "[[still-code]]" not in out
    assert "[[live]]" in out


def test_strip_code_spans_short_fence_does_not_close_longer():
    # A 3-backtick line must NOT close a 4-backtick-opened block.
    out = _md.strip_code_spans("````\n```\n[[still-code]]\n````\n[[live]]")
    assert "[[still-code]]" not in out
    assert "[[live]]" in out


# ---------- find_wikilink_refs ----------

def test_find_wikilink_refs_marks_embeds():
    refs = _md.find_wikilink_refs("a [[link]] and an ![[embed.png]]")
    assert ("link", False) in refs
    assert ("embed.png", True) in refs


def test_find_wikilink_refs_strips_alias_and_heading():
    assert _md.find_wikilink_refs("see [[page#section|Display]]") == [("page", False)]
# ---------- Markdown links ----------

def test_parse_markdown_links_basic():
    text = "see [Foo](path/to/foo.md) and [Bar](bar.md)"
    links = _md.parse_markdown_links(text)
    assert len(links) == 2
    assert links[0].label == "Foo"
    assert links[0].href == "path/to/foo.md"
    assert links[0].anchor == ""
    assert links[1].href == "bar.md"


def test_parse_markdown_links_anchor():
    text = "[a](page.md#section) text"
    links = _md.parse_markdown_links(text)
    assert len(links) == 1
    assert links[0].href == "page.md"
    assert links[0].anchor == "#section"


def test_parse_markdown_links_skips_wikilinks():
    text = "[[foo]] and [bar](bar.md)"
    links = _md.parse_markdown_links(text)
    assert len(links) == 1
    assert links[0].href == "bar.md"


def test_resolve_markdown_link_skips_url():
    from pathlib import Path
    assert _md.resolve_markdown_link("https://x.com", Path("/w/p.md"), Path("/w")) is None


def test_resolve_markdown_link_skips_anchor():
    from pathlib import Path
    assert _md.resolve_markdown_link("#section", Path("/w/p.md"), Path("/w")) is None


def test_resolve_markdown_link_relative(tmp_path):
    wiki = tmp_path / "wiki"
    (wiki / "projects").mkdir(parents=True)
    (wiki / "projects" / "foo.md").write_text("# foo")
    (wiki / "projects" / "other.md").write_text("# other")
    target = _md.resolve_markdown_link("foo.md", wiki / "projects" / "other.md", wiki)
    assert target == (wiki / "projects" / "foo.md").resolve()


def test_resolve_markdown_link_walks_up(tmp_path):
    wiki = tmp_path / "wiki"
    (wiki / "projects" / "deep").mkdir(parents=True)
    (wiki / "projects" / "deep" / "page.md").write_text("# page")
    (wiki / "global.md").write_text("# global")
    target = _md.resolve_markdown_link(
        "../../global.md",
        wiki / "projects" / "deep" / "page.md",
        wiki,
    )
    assert target == (wiki / "global.md").resolve()


def test_compute_relative_link_same_dir(tmp_path):
    target = tmp_path / "a" / "target.md"
    src = tmp_path / "a" / "src.md"
    assert _md.compute_relative_link(target, src) == "target.md"


def test_compute_relative_link_up_one(tmp_path):
    target = tmp_path / "target.md"
    src = tmp_path / "sub" / "src.md"
    assert _md.compute_relative_link(target, src) == "../target.md"


def test_compute_relative_link_down(tmp_path):
    target = tmp_path / "sub" / "target.md"
    src = tmp_path / "src.md"
    assert _md.compute_relative_link(target, src) == "sub/target.md"


# ---------- parse_wikilink_full ----------

def test_parse_wikilink_simple():
    out = _md.parse_wikilink_full("see [[foo]] here")
    assert len(out) == 1
    assert out[0].target == "foo"
    assert out[0].anchor == ""
    assert out[0].display is None


def test_parse_wikilink_with_display():
    out = _md.parse_wikilink_full("[[foo|My Foo]]")
    assert out[0].target == "foo"
    assert out[0].display == "My Foo"


def test_parse_wikilink_with_anchor():
    out = _md.parse_wikilink_full("[[foo#section]]")
    assert out[0].target == "foo"
    assert out[0].anchor == "section"
    assert out[0].display is None


def test_parse_wikilink_with_anchor_and_display():
    out = _md.parse_wikilink_full("[[foo#section|Display]]")
    assert out[0].target == "foo"
    assert out[0].anchor == "section"
    assert out[0].display == "Display"


def test_parse_wikilink_pathful():
    out = _md.parse_wikilink_full("[[projects/foo]] and [[projects/foo|Display]]")
    assert len(out) == 2
    assert out[0].target == "projects/foo"
    assert out[1].target == "projects/foo"
    assert out[1].display == "Display"


# ---------- parse_frontmatter_aliases ----------

def test_parse_aliases_returns_list_block():
    text = "---\naliases:\n  - foo\n  - bar\n---\n# body"
    assert _md.parse_frontmatter_aliases(text) == ["foo", "bar"]


def test_parse_aliases_returns_list_inline():
    text = "---\naliases: [foo, bar]\n---\n# body"
    assert _md.parse_frontmatter_aliases(text) == ["foo", "bar"]


def test_parse_aliases_returns_empty_when_field_absent():
    text = "---\ntitle: foo\n---\n# body"
    assert _md.parse_frontmatter_aliases(text) == []


def test_parse_aliases_returns_empty_when_no_frontmatter():
    text = "# body only"
    assert _md.parse_frontmatter_aliases(text) == []


# ---------- frontmatter_add_alias ----------

def test_add_alias_creates_frontmatter_when_absent():
    text = "# body only\n"
    new, added = _md.frontmatter_add_alias(text, "foo")
    assert added is True
    assert _md.parse_frontmatter_aliases(new) == ["foo"]
    assert "# body only" in new


def test_add_alias_adds_field_when_aliases_missing():
    text = "---\ntitle: Hello\n---\n# body\n"
    new, added = _md.frontmatter_add_alias(text, "foo")
    assert added is True
    assert _md.parse_frontmatter_aliases(new) == ["foo"]
    assert _md.parse_frontmatter(new)["title"] == "Hello"


def test_add_alias_appends_to_block_list():
    text = "---\naliases:\n  - foo\n  - bar\n---\n# body\n"
    new, added = _md.frontmatter_add_alias(text, "baz")
    assert added is True
    assert _md.parse_frontmatter_aliases(new) == ["foo", "bar", "baz"]


def test_add_alias_appends_to_inline_list():
    text = "---\naliases: [foo, bar]\n---\n# body\n"
    new, added = _md.frontmatter_add_alias(text, "baz")
    assert added is True
    aliases = _md.parse_frontmatter_aliases(new)
    assert aliases == ["foo", "bar", "baz"]
    assert "aliases: [foo, bar, baz]" in new


def test_add_alias_no_op_when_present_exact():
    text = "---\naliases:\n  - foo\n---\n# body"
    new, added = _md.frontmatter_add_alias(text, "foo")
    assert added is False
    assert new == text


def test_add_alias_no_op_when_present_case_insensitive():
    """Matches Obsidian's case-insensitive autocomplete and the wiki-level
    collision preflight in space.py."""
    text = "---\naliases:\n  - Foo\n---\n# body"
    new, added = _md.frontmatter_add_alias(text, "foo")
    assert added is False
    assert new == text


def test_add_alias_preserves_other_fields():
    text = (
        "---\n"
        "title: Hello\n"
        "summary: A test\n"
        "tags: [a, b]\n"
        "aliases:\n  - foo\n"
        "---\n"
        "# body\n"
    )
    new, _added = _md.frontmatter_add_alias(text, "bar")
    fm = _md.parse_frontmatter(new)
    assert fm["title"] == "Hello"
    assert fm["summary"] == "A test"
    assert fm["tags"] == ["a", "b"]
    assert fm["aliases"] == ["foo", "bar"]
