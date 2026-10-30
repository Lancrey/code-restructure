import unittest
from helpers import make_repo, git, run_script, write


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
        self.assertIn("pages/index.html:2  ../img/bg.png", out)
        self.assertIn("pages/index.html:4  url(../img/bg.png)", out)
        self.assertNotIn("an img tag", out)  # the word, not the path

    def test_moved_stylesheet_breaks_its_own_relative_urls(self):
        r = self.site()
        moved(r, "css/site.css", "assets/css/site.css")
        code, out = run_script("check_moves.py", r, "--moves", "css/site.css=assets/css/site.css")
        self.assertEqual(code, 1, out)
        self.assertIn("assets/css/site.css:1  url(../img/bg.png)", out)
        self.assertIn("assets/css/site.css:3  @import print.css", out)
        self.assertIn("pages/index.html:1  ../css/site.css", out)

    def test_sass_partial_and_index_conventions(self):
        r = self.site()
        moved(r, "scss/partials/_vars.scss", "scss/tokens/_vars.scss")
        code, out = run_script("check_moves.py", r, "--moves", "scss/partials/_vars.scss=scss/tokens/_vars.scss")
        self.assertEqual(code, 1, out)
        self.assertIn("scss/main.scss:1  @import partials/vars", out)
        self.assertNotIn("partials/mixins", out)
        self.assertNotIn("legacy", out)


if __name__ == "__main__":
    unittest.main()
