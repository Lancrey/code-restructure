import unittest
from helpers import make_repo, git, run_script


def moved(root, src, dst):
    (root / dst).parent.mkdir(parents=True, exist_ok=True)
    git(root, "mv", src, dst)


class FrontendAssets(unittest.TestCase):
    def site(self):
        return make_repo({
            "css/site.css": 'body{background:url(../img/bg.png)}\n@font-face{src:url("../fonts/a.woff2") format("woff2")}\n'
                            '@import "print.css";\n.x{background:url(data:image/png;base64,AAA)} .y{background:url(/static/root.png)}\n',
            "css/print.css": "", "img/bg.png": "", "fonts/a.woff2": "",
            "scss/main.scss": '@use "partials/vars";\n@use "partials/mixins";\n@import "legacy";\n',
            "scss/partials/_vars.scss": "", "scss/partials/mixins/_index.scss": "", "scss/_legacy.scss": "",
            "pages/index.html": '<link rel="stylesheet" href="../css/site.css">\n<img src="../img/bg.png" srcset="../img/bg.png 1x, ../img/bg.png 2x">\n'
                                '<a href="/about">x</a> <a href="https://x.y/">y</a> <img src="{{ asset }}"> <p>an img tag</p>\n'
                                '<div style="background:url(../img/bg.png)"></div>\n',
            "zz/b.py": "",
        })

    def test_consistent_site_is_clean(self):
        code, out = run_script("check_moves.py", self.site(), "--moves", "zz/a.py=zz/b.py")
        self.assertEqual(code, 0, out)

    def test_moved_asset_folder_breaks_css_and_html(self):
        r = self.site()
        moved(r, "img/bg.png", "assets/img/bg.png")
        code, out = run_script("check_moves.py", r, "--moves", "img=assets/img")
        self.assertEqual(code, 1, out)
        self.assertIn("css/site.css:1  url(../img/bg.png)   <- was moved to assets/img/bg.png", out)
        self.assertIn("pages/index.html:2  src=../img/bg.png", out)
        self.assertIn("pages/index.html:4  url(../img/bg.png)", out)
        self.assertNotIn("an img tag", out)  # the word, not the path

    def test_moved_stylesheet_breaks_its_own_relative_urls(self):
        r = self.site()
        moved(r, "css/site.css", "assets/css/site.css")
        code, out = run_script("check_moves.py", r, "--moves", "css/site.css=assets/css/site.css")
        self.assertEqual(code, 1, out)
        self.assertIn("assets/css/site.css:1  url(../img/bg.png)", out)
        self.assertIn("assets/css/site.css:3  @import print.css", out)
        self.assertIn("pages/index.html:1  href=../css/site.css", out)

    def test_sass_partial_and_index_conventions(self):
        r = self.site()
        moved(r, "scss/partials/_vars.scss", "scss/tokens/_vars.scss")
        code, out = run_script("check_moves.py", r, "--moves", "scss/partials/_vars.scss=scss/tokens/_vars.scss")
        self.assertEqual(code, 1, out)
        self.assertIn("scss/main.scss:1  @import partials/vars", out)
        self.assertNotIn("partials/mixins", out)
        self.assertNotIn("legacy", out)

    def test_node_modules_accepts_only_existing_packages(self):
        r = make_repo({"scss/main.scss": '@use "bootstrap/scss/bootstrap";\n@use "notapkg/x";\n@use "partials/vars";\n',
                       "node_modules/bootstrap/scss/_bootstrap.scss": "", "scss/partials/_vars.scss": "", "zz/b.py": ""})
        moved(r, "scss/partials/_vars.scss", "scss/tokens/_vars.scss")
        code, out = run_script("check_moves.py", r, "--moves", "scss/partials/_vars.scss=scss/tokens/_vars.scss")
        self.assertEqual(code, 1, out)
        self.assertIn("@import partials/vars", out)
        self.assertIn("@import notapkg/x", out)
        self.assertNotIn("bootstrap", out)

    def test_sass_url_is_uncertain_not_a_failure(self):
        r = make_repo({"src/scss/main.scss": "body{background:url(../img/bg.png)}\n", "public/img/bg.png": "", "zz/b.py": ""})
        code, out = run_script("check_moves.py", r, "--moves", "zz/a.py=zz/b.py")
        self.assertEqual(code, 0, out)
        self.assertIn("resolves against the compiled CSS", out)
        self.assertIn("src/scss/main.scss:1  url(../img/bg.png)", out)

    def test_template_routes_are_not_files(self):
        r = make_repo({"templates/t.twig": '<a href="contact">c</a> <a href="../about">a</a> <a href="page.html">p</a>\n'
                                           '<link rel="icon" href="fav.ico"> <img srcset="a.png 1x, b.png 2x">\n', "templates/a.png": "", "zz/b.py": ""})
        code, out = run_script("check_moves.py", r, "--moves", "zz/a.py=zz/b.py", "--all-unresolved")
        self.assertNotIn("href=contact", out)
        self.assertNotIn("href=../about", out)
        self.assertIn("href=page.html", out)
        self.assertIn("href=fav.ico", out)
        self.assertIn("srcset=b.png", out)
        self.assertNotIn("srcset=a.png", out)

    def test_vue_sfc_gets_script_template_and_style_checks(self):
        r = make_repo({"comp/A.vue": '<script>\nimport X from "./X.js";\n</script>\n<template><img src="./logo.png"></template>\n<style>.a{background:url(./bg.png)}</style>\n', "zz/b.py": ""})
        code, out = run_script("check_moves.py", r, "--moves", "zz/a.py=zz/b.py", "--all-unresolved")
        for spec in ("./X.js", "src=./logo.png", "url(./bg.png)"):
            self.assertIn(spec, out)

    def test_before_file_turns_new_unresolved_into_failure(self):
        r = make_repo({"site/index.html": '<img src="img/a.png"><img src="img/old-missing.png">\n', "site/img/a.png": "", "zz/b.py": ""})
        code, out = run_script("check_moves.py", r, "--moves", "zz/a.py=zz/b.py", "--json", r / "before.json")
        self.assertEqual(code, 0, out)  # old-missing is pre-existing: no failure
        moved(r, "site/img/a.png", "site/assets/a.png")  # a move NOT in the plan
        code, out = run_script("check_moves.py", r, "--moves", "zz/a.py=zz/b.py", "--before", r / "before.json")
        self.assertEqual(code, 1, out)
        self.assertIn("resolved on the original commit", out)
        self.assertIn("src=img/a.png", out)

    def test_dated_reports_are_allowlisted(self):
        r = make_repo({"docs/architecture-audit-2026-10-30.md": "old path app/db.py\n", "app/db.py": ""})
        moved(r, "app/db.py", "app/data/db.py")
        code, out = run_script("check_moves.py", r, "--moves", "app/db.py=app/data/db.py", "--no-imports")
        self.assertEqual(code, 0, out)

    def test_expand_globs_prints_concrete_rows(self):
        r = make_repo({"plugins/a.py": "", "plugins/b.py": "", "plan.md": "| From | To | Note |\n|---|---|---|\n| plugins/*.py | src/plugins/<name>/main.py | |\n"})
        code, out = run_script("check_moves.py", r, "--plan", "plan.md", "--expand-globs")
        self.assertEqual(code, 0, out)
        self.assertIn("| plugins/a.py | src/plugins/a/main.py |", out)
        self.assertIn("| plugins/b.py | src/plugins/b/main.py |", out)

    def test_package_present_but_file_missing_is_unresolved(self):
        r = make_repo({"scss/main.scss": '@use "bootstrap/scss/bootstrap";\n@use "bootstrap/scss/nope";\n@use "bootstrap";\n',
                       "node_modules/bootstrap/scss/_bootstrap.scss": "", "zz/b.py": ""})
        code, out = run_script("check_moves.py", r, "--moves", "zz/a.py=zz/b.py", "--all-unresolved")
        self.assertIn("@import bootstrap/scss/nope", out)
        self.assertIn("@import bootstrap (package entry point", out)  # uncertain, not broken
        self.assertNotIn("@import bootstrap/scss/bootstrap", out)

    def test_vue_style_lang_scss_resolves_partials(self):
        r = make_repo({"comp/A.vue": '<template><img src="./logo.png"></template>\n<style lang="scss">\n@use "./tokens/colors";\n@use "./tokens/missing";\n</style>\n',
                       "comp/tokens/_colors.scss": "", "comp/logo.png": "", "zz/b.py": ""})
        code, out = run_script("check_moves.py", r, "--moves", "zz/a.py=zz/b.py", "--all-unresolved")
        self.assertNotIn("tokens/colors", out)
        self.assertIn("@import ./tokens/missing", out)

    def test_template_relative_assets_are_uncertain_not_broken(self):
        r = make_repo({"templates/page.twig": '<img src="img/x.png"> <link rel="stylesheet" href="css/a.css">\n', "zz/b.py": ""})
        code, out = run_script("check_moves.py", r, "--moves", "zz/a.py=zz/b.py")
        self.assertEqual(code, 0, out)
        self.assertIn("server-rendered templates", out)
        self.assertIn("src=img/x.png", out)

    def test_preexisting_break_in_a_moved_file_is_not_blamed_on_the_move(self):
        r = make_repo({"css/site.css": "a{background:url(../img/gone.png)}\n", "zz/b.py": ""})
        code, out = run_script("check_moves.py", r, "--moves", "zz/a.py=zz/b.py", "--json", r / "before.json")
        (r / "assets").mkdir()
        git(r, "mv", "css", "assets/css")  # move the whole directory, as the plan row says
        code, out = run_script("check_moves.py", r, "--moves", "css=assets/css", "--before", r / "before.json", "--allow", "before.json")
        self.assertEqual(code, 0, out)
        self.assertNotIn("broken by the listed moves", out)
        self.assertIn("1 unresolved reference(s) already unresolved on the original commit", out)


if __name__ == "__main__":
    unittest.main()
