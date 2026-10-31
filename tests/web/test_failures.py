from apparadise.failures import excerpt, first_error_line, normalise_error, scrub, signature


def test_gprofng_across_binutils_versions_is_one_group():
    """FAILURES.md #13/#17/#19: the same break in three binutils easyconfigs."""
    a = "make: *** [Makefile:1028: all-gprofng] Error 2"
    b = "make: *** [Makefile:7317: all-gprofng] Error 2"
    assert signature("build", a) == signature("build", b)


def test_versions_and_paths_normalised():
    a = "/storage/x/eb/build/gettext/0.25/string.h:777:20: error: expected identifier"
    b = "/scratch/eb-build/gettext/0.26/string.h:777:20: error: expected identifier"
    assert normalise_error(a) == normalise_error(b)
    assert signature("build", a) == signature("build", b)
    assert signature("build", a) != signature("test", a)


def test_restricted_never_shares_a_group():
    assert signature("build", "error: x") != signature("build", "error: x", restricted=True)


def test_first_error_line():
    log = "checking for gcc... yes\nconfigure: error: Cannot find socklen_t\nmore\n"
    assert first_error_line(log) == "configure: error: Cannot find socklen_t"
    assert first_error_line("all good\nlast line") == "last line"
    assert first_error_line("") == ""


def test_scrub():
    text = ("cd /home/alice/secret-project && curl -H 'Authorization: Bearer abc.def' "
            "https://user:pw@mirror.example/x token=s3cr3t node-17.fhcrc.org 10.1.2.3")
    out = scrub(text)
    for leaked in ("alice", "abc.def", "user:pw", "s3cr3t", "node-17", "10.1.2.3"):
        assert leaked not in out, leaked


def test_excerpt_centres_on_error():
    log = "\n".join(f"line {i}" for i in range(200)) + "\nerror: boom\n" + "tail\n" * 5
    ex = excerpt(log, around="error: boom")
    assert "error: boom" in ex and len(ex.splitlines()) <= 30


def test_scrub_removes_the_whole_internal_host_name():
    out = scrub("srun: error: gpu-n12.cluster.fredhutch.org: task 0: Exited")
    assert "gpu-n12" not in out and "<host>" in out
