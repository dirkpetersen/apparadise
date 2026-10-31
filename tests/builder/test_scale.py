from apparadise_builder.scale import plan, scale_once


class L:
    def __init__(self):
        self.n = 0

    def start(self):
        self.n += 1
        return f"job{self.n}"

    def stop(self, launch_id):
        pass


def test_plan():
    assert plan(0, 0, 4) == 0
    assert plan(3, 0, 4) == 3
    assert plan(10, 1, 4) == 3
    assert plan(2, 3, 4) == 0


def test_scale_once_remembers_and_expires(tmp_path):
    state = tmp_path / "s.json"
    counts = {"queued": {"linux/arm64": 5, "linux/amd64": 9}}
    lc = L()
    assert scale_once(counts, "linux/arm64", lc, state, 2, 100, now=0) == ["job1", "job2"]
    assert scale_once(counts, "linux/arm64", lc, state, 2, 100, now=50) == []  # at max
    assert scale_once(counts, "linux/arm64", lc, state, 2, 100, now=200) == ["job3", "job4"]
    assert state.stat().st_mode & 0o777 == 0o600
