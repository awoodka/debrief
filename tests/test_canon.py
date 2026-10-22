import pytest

from debrief import canon, sanitize


@pytest.mark.parametrize("url, expected", [
    ("HTTP://WWW.Example.com:443/a/b/?utm_source=x&id=7&ref=hn#frag", "https://example.com/a/b?id=7"),
    ("https://example.com/", "https://example.com/"),
    ("https://m.example.com//a//b/", "https://example.com/a/b"),
    ("https://twitter.com/karpathy/status/1", "https://x.com/karpathy/status/1"),
    ("https://example.com:8080/x", "https://example.com:8080/x"),
    ("mailto:someone@example.com", ""),
    ("not a url", ""),
])
def test_canonical_url(url, expected):
    assert canon.canonical_url(url) == expected


@pytest.mark.parametrize("url, expected", [
    ("https://arxiv.org/abs/2610.12345v2", "arxiv:2610.12345"),
    ("http://arxiv.org/pdf/2610.12345v1.pdf", "arxiv:2610.12345"),
    ("https://arxiv.org/html/2610.12345", "arxiv:2610.12345"),
    ("https://export.arxiv.org/abs/hep-th/9901001", "arxiv:hep-th/9901001"),
    ("https://www.alphaxiv.org/abs/2610.12345", "arxiv:2610.12345"),
    ("https://huggingface.co/papers/2610.12345", "arxiv:2610.12345"),
    ("https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash", "hf-model:deepseek-ai/deepseek-v4.1-flash"),
    ("https://huggingface.co/spaces/Org/Demo", "hf-space:org/demo"),
    ("https://huggingface.co/datasets/lmarena-ai/leaderboard-dataset", "hf-dataset:lmarena-ai/leaderboard-dataset"),
    ("https://huggingface.co/blog/some-post", None),
    ("https://github.com/vllm-project/vllm/releases/tag/v0.32.0", "gh-release:vllm-project/vllm@v0.32.0"),
    ("https://github.com/Vllm-Project/VLLM.git", "gh-repo:vllm-project/vllm"),
    ("https://github.com/vllm-project/vllm/tree/main/docs", "gh-repo:vllm-project/vllm"),
    ("https://github.com/trending", None),
    ("https://pypi.org/project/Huggingface_Hub/2.2.0/", "pkg:pypi/huggingface-hub@2.2.0"),
    ("https://www.npmjs.com/package/@anthropic-ai/sdk/v/1.1.0", "pkg:npm/@anthropic-ai/sdk@1.1.0"),
    ("https://www.npmjs.com/package/openai", "pkg:npm/openai"),
])
def test_story_id(url, expected):
    got = canon.story_id(url)
    if expected is None:
        assert got.startswith("url:") and len(got) == 20
    else:
        assert got == expected


def test_url_ids_ignore_tracking_noise():
    assert canon.story_id("https://openai.com/index/x/?utm_source=rss") == canon.story_id("https://openai.com/index/x")


def test_arxiv_id_in_text():
    assert canon.arxiv_id("see arXiv:2610.28708v1 for details") == "2610.28708"
    assert canon.arxiv_id("version 12610.123456 is not an id") is None


def test_sanitize_strips_markup_invisible_and_caps():
    s = sanitize.text("<p>Hello&nbsp;<b>world</b></p>​‮evil\x07", max_chars=100)
    assert s == "Hello world evil"
    assert sanitize.text("word " * 50, max_chars=20).endswith("…")
    assert len(sanitize.text("word " * 50, max_chars=20)) <= 20
    assert sanitize.text("<p>One</p><p>Two</p>", keep_paragraphs=True) == "One\n\nTwo"
