# tests/test_run_youzi.py
# -*- coding: utf-8 -*-
"""run_youzi.py 主管线闭环:fetch → render → verify → deliver 顺序 +
verify 硬失败不交付 + deliver 缺字段不崩溃。真实网络/子进程全部 mock。"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts import fetch, run_youzi  # noqa: E402
import verify  # noqa: E402


def _write_analysis(tmp_path, data=None):
    analysis = tmp_path / "03-analysis.json"
    analysis.write_text(
        json.dumps(
            data
            if data is not None
            else {
                "topic": "测试主题",
                "competitors": [{"name": "WATI"}],
                "opportunities": [
                    {"title": "机会A", "disrupt_score": 8, "inspiration": "来自证据"}
                ],
                "executive_summary": "TL;DR",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return analysis


def _patch_pipeline(monkeypatch, calls, verify_result):
    """mock fetch/render/verify/deliver 四步,calls 记录调用顺序。"""

    def fake_fetch(name, out_dir, budget_s=None, topic=""):
        calls.append(("fetch", name, topic))
        return {
            "name": name,
            "url": f"https://{name}",
            "pages": {"homepage": {"sufficient": True, "problems": []}},
            "failures": [],
        }

    def fake_render(analysis_path, output_path, template_path=None):
        calls.append(("render",))
        Path(output_path).write_text("<html></html>", encoding="utf-8")

    def fake_verify(analysis_path, manifest_path, raw_dir, **kw):
        calls.append(("verify",))
        return verify_result

    monkeypatch.setattr(fetch, "fetch_competitor", fake_fetch)
    monkeypatch.setattr(run_youzi, "step4_render", fake_render)
    monkeypatch.setattr(verify, "verify_analysis", fake_verify)


def _run_main(monkeypatch, argv):
    monkeypatch.setattr(sys, "argv", ["run_youzi.py"] + argv)
    return run_youzi.main()


def test_happy_path_order_fetch_render_verify_deliver(tmp_path, monkeypatch):
    calls = []
    _patch_pipeline(
        monkeypatch,
        calls,
        {
            "passed": True,
            "exit_code": 0,
            "summary": {"hard_failed": 0, "warnings": 0},
            "violations": [],
            "warnings": [],
        },
    )
    monkeypatch.setattr(
        run_youzi, "step6_deliver", lambda *a: calls.append(("deliver",))
    )
    analysis = _write_analysis(tmp_path)
    out = tmp_path / "report.html"
    rc = _run_main(
        monkeypatch,
        [
            "--topic",
            "测试主题",
            "--competitors",
            "wati.io,respond.io",
            "--raw-dir",
            str(tmp_path / "02-raw"),
            "--analysis",
            str(analysis),
            "--output",
            str(out),
        ],
    )
    assert rc == 0
    steps = [c[0] for c in calls]
    assert steps == ["fetch", "fetch", "render", "verify", "deliver"]
    # topic 透传到 fetch(不再硬编码空串)
    assert calls[0][2] == "测试主题"


def test_verify_hard_fail_exits_nonzero_and_no_deliver(tmp_path, monkeypatch):
    calls = []
    _patch_pipeline(
        monkeypatch,
        calls,
        {
            "passed": False,
            "exit_code": 2,
            "summary": {"hard_failed": 1, "warnings": 0},
            "violations": [
                {
                    "gate": "G2",
                    "field": "competitors[0].pricing",
                    "source_url": "https://wati.io/pricing",
                    "detail": "quote 未命中",
                    "hint": "重跑 Step 3 补证据",
                }
            ],
            "warnings": [],
        },
    )
    monkeypatch.setattr(
        run_youzi, "step6_deliver", lambda *a: calls.append(("deliver",))
    )
    analysis = _write_analysis(tmp_path)
    rc = _run_main(
        monkeypatch,
        [
            "--topic",
            "测试主题",
            "--competitors",
            "wati.io",
            "--raw-dir",
            str(tmp_path / "02-raw"),
            "--analysis",
            str(analysis),
            "--output",
            str(tmp_path / "report.html"),
        ],
    )
    assert rc == 2, "verify 硬失败应以 exit 2 语义退出"
    assert "deliver" not in [c[0] for c in calls], "verify 不过 = 不交付"


def test_verify_missing_manifest_exits_nonzero(tmp_path, monkeypatch):
    """无证据包(analysis-only 且旁边无 manifest)→ verify exit 1 语义,不交付。"""
    calls = []

    def fake_render(analysis_path, output_path, template_path=None):
        Path(output_path).write_text("<html></html>", encoding="utf-8")
        calls.append(("render",))

    monkeypatch.setattr(run_youzi, "step4_render", fake_render)
    monkeypatch.setattr(
        run_youzi, "step6_deliver", lambda *a: calls.append(("deliver",))
    )
    analysis = _write_analysis(tmp_path)
    rc = _run_main(
        monkeypatch,
        [
            "--topic",
            "测试主题",
            "--analysis",
            str(analysis),
            "--output",
            str(tmp_path / "report.html"),
        ],
    )
    assert rc == 1
    assert "deliver" not in [c[0] for c in calls]


def test_deliver_tolerates_missing_topic_and_title(tmp_path, capsys):
    """analysis 缺 topic / opportunities 缺 title / 报告文件不存在 → 不崩溃。"""
    analysis = _write_analysis(
        tmp_path,
        data={
            "competitors": [],
            "opportunities": [{"disrupt_score": 5, "inspiration": "x" * 200}],
        },
    )
    # 报告不存在(render rc==2 隐式契约不可盲信)
    run_youzi.step6_deliver(analysis, tmp_path / "nope.html")
    out = capsys.readouterr().out
    assert "(未提供主题)" in out
    assert "(无标题)" in out
    # 报告存在时正常打印大小
    report = tmp_path / "ok.html"
    report.write_text("<html></html>", encoding="utf-8")
    run_youzi.step6_deliver(analysis, report)
    assert "KB" in capsys.readouterr().out


def test_fetch_cname_collision_gets_hash_suffix(tmp_path, monkeypatch):
    """同域不同 URL(同 canonical_name)→ 第二个竞品落盘加短 hash,不覆盖第一个。"""

    def fake_resolve(name):
        base = "https://dup.io" if name == "dup.io" else "https://dup.io/pricing"
        return {
            "name": name,
            "canonical_name": "dup.io",  # 两个竞品同一 cname
            "url": base,
            "pricing_url": None,  # 不猜定价页,避免触发升级梯重爬
            "features_url": None,
            "docs_url": None,
            "source": "domain-guess",
            "confidence": 0.4,
        }

    def fake_scrape_smart(url, enabled_scrapers=None, **kw):
        return {
            "success": True,
            "scraper": "jina",
            "markdown": f"content of {url} " * 30,
            "all_results": [
                {
                    "success": True,
                    "scraper": "jina",
                    "markdown": f"content of {url} " * 30,
                }
            ],
            "stats": {"successful": 1},
        }

    monkeypatch.setattr(fetch, "resolve_competitor", fake_resolve)
    monkeypatch.setattr(fetch, "scrape_smart", fake_scrape_smart)

    fetch.fetch_competitor("dup.io", tmp_path)
    fetch.fetch_competitor("dup.io/pricing", tmp_path)

    raw_files = sorted(p.name for p in (tmp_path / "02-raw").glob("*.engines.json"))
    assert len(raw_files) == 2, f"同 cname 冲突应各自落盘: {raw_files}"
    assert raw_files[0] == "dup_io.engines.json"
    assert raw_files[1].startswith("dup_io_") and raw_files[1].endswith(".engines.json")


def test_fetch_topic_passthrough_to_manifest(tmp_path, monkeypatch):
    """fetch 侧 topic 透传:manifest.run.topic 不再硬编码空串。"""

    def fake_resolve(name):
        return {
            "name": name,
            "canonical_name": name,
            "url": "https://wati.io",
            "pricing_url": None,
            "features_url": None,
            "docs_url": None,
            "source": "domain-guess",
            "confidence": 0.4,
        }

    def fake_scrape_smart(url, enabled_scrapers=None, **kw):
        return {
            "success": True,
            "scraper": "jina",
            "markdown": "home content " * 30,
            "all_results": [
                {"success": True, "scraper": "jina", "markdown": "home content " * 30}
            ],
            "stats": {"successful": 1},
        }

    monkeypatch.setattr(fetch, "resolve_competitor", fake_resolve)
    monkeypatch.setattr(fetch, "scrape_smart", fake_scrape_smart)

    fetch.fetch_competitor("wati.io", tmp_path, topic="WhatsApp 运营")
    manifest = json.loads((tmp_path / "claims-manifest.json").read_text())
    assert manifest["run"]["topic"] == "WhatsApp 运营"
