"""Tide Pool puzzle: rotate pipes so water runs from the sea to Yo-chan's pool."""
import random

# sides as bits
N, E, S, W = 1, 2, 4, 8
DIRS = [(N, 0, -1), (E, 1, 0), (S, 0, 1), (W, -1, 0)]
OPP = {N: S, E: W, S: N, W: E}
FILLER = [10, 3, 3, 3, 11]  # straight, corners, tee for tiles off the path


def rot(m: int) -> int:
    return ((m << 1) | (m >> 3)) & 15


def rotations(m: int) -> set[int]:
    out = {m}
    for _ in range(3):
        m = rot(m)
        out.add(m)
    return out


def make_puzzle(w: int = 5, h: int = 4, rng: random.Random | None = None) -> dict:
    rng = rng or random.Random()
    r0 = rng.randrange(h)
    path: list[tuple[int, int]] = []

    # random walk until a long-enough path reaches the right column
    def dfs(x: int, y: int, acc: list, seen: set) -> bool:
        acc.append((x, y))
        seen.add((x, y))
        if x == w - 1 and len(acc) >= w + 3:
            path.extend(acc)
            return True
        dirs = DIRS[:]
        rng.shuffle(dirs)
        for _, dx, dy in dirs:
            nx, ny = x + dx, y + dy
            if 0 <= nx < w and 0 <= ny < h and (nx, ny) not in seen and dfs(nx, ny, acc, seen):
                return True
        acc.pop()
        seen.discard((x, y))
        return False

    dfs(0, r0, [], set())
    sol = [[rng.choice(FILLER) for _ in range(w)] for _ in range(h)]

    def side(a, b):
        return next(bit for bit, dx, dy in DIRS if (a[0] + dx, a[1] + dy) == b)

    for i, (x, y) in enumerate(path):
        into = W if i == 0 else side((x, y), path[i - 1])
        out = E if i == len(path) - 1 else side((x, y), path[i + 1])
        sol[y][x] = into | out
    p = {"w": w, "h": h, "r0": r0, "r1": path[-1][1], "sol": sol, "best": len(path)}
    # scramble until the starting board doesn't already win
    while True:
        grid = [[m for m in row] for row in sol]
        for row in grid:
            for x, m in enumerate(row):
                for _ in range(rng.randrange(4)):
                    m = rot(m)
                row[x] = m
        if not flow_check(p, grid)["won"]:
            p["grid"] = grid
            return p


def flow_check(p: dict, grid: list[list[int]]) -> dict:
    """Water from the left of row r0; won if it leaves the right of row r1."""
    w, h, r0, r1 = p["w"], p["h"], p["r0"], p["r1"]
    order: list[tuple[int, int]] = []
    wet = set()
    won = False
    if grid[r0][0] & W:
        queue = [(0, r0)]
        wet.add((0, r0))
        while queue:
            x, y = queue.pop(0)
            order.append((x, y))
            for bit, dx, dy in DIRS:
                if not grid[y][x] & bit or (x == 0 and y == r0 and bit == W):
                    continue
                if x == w - 1 and y == r1 and bit == E:
                    won = True
                    continue
                nx, ny = x + dx, y + dy
                if 0 <= nx < w and 0 <= ny < h and grid[ny][nx] & OPP[bit] and (nx, ny) not in wet:
                    wet.add((nx, ny))
                    queue.append((nx, ny))
    return {"won": won, "order": order, "tiles": len(order)}


def valid_grid(p: dict, grid) -> bool:
    # a submitted board may only rotate the tiles it was dealt
    try:
        return len(grid) == p["h"] and all(
            len(row) == p["w"] and all(isinstance(m, int) and m in rotations(p["grid"][y][x]) for x, m in enumerate(row))
            for y, row in enumerate(grid)
        )
    except (TypeError, KeyError):
        return False
