import pytest

from apparadise_builder.launchers import Ec2Spot, LocalContainer, Sbatch


class Rec:
    def __init__(self, out="id-1"):
        self.calls, self.out = [], out

    def __call__(self, argv):
        self.calls.append(list(argv))
        return self.out


def test_local_container():
    r = Rec("abc123")
    lc = LocalContainer("reg/cache/builder-agent:1", "/etc/b.env", "/srv/d", "/scratch/a",
                        runner=r)
    assert lc.start() == "abc123"
    assert r.calls[0][:4] == ["podman", "run", "-d", "--rm"]
    lc.stop("abc123")
    assert r.calls[1] == ["podman", "stop", "abc123"]


def test_sbatch_parses_job_id():
    r = Rec("4242;cluster")
    s = Sbatch("build", "/srv/builder", runner=r)
    assert s.start() == "4242"
    assert "--wrap=.venv/bin/python3 app.py" in r.calls[0]
    s.stop("4242")
    assert r.calls[1] == ["scancel", "4242"]


def test_ec2_spot():
    r = Rec("i-0abc")
    e = Ec2Spot("apparadise-builder-arm", "us-west-2", runner=r)
    assert e.start() == "i-0abc"
    assert "MarketType=spot" in r.calls[0]
    assert r.calls[0][:4] == ["aws", "ec2", "--region", "us-west-2"]
    e.stop("i-0abc")
    assert r.calls[1][-1] == "i-0abc"


@pytest.mark.parametrize("bad", ["x; rm -rf /", "$(id)", "a b", "", "--wrap=evil", "ok\n"])
def test_unsafe_values_refused(bad):
    with pytest.raises(ValueError):
        Sbatch(bad, "/w", runner=Rec())
    with pytest.raises(ValueError):
        Sbatch("p", "/w", runner=Rec()).stop(bad)
