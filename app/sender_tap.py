"""Reading a gauge's sender wire without disconnecting the gauge -- a "passive tap".

The boat's analog gauges stay wired exactly as they are and keep working. The sensor board reads
the voltage on each gauge's sender (S) terminal through a high-impedance divider, and the voltage
on the gauge's own supply (I) terminal, and this module turns those into a level. See
SENSOR_BOARD.md for the circuit.

Why it is not a straight line
-----------------------------
Inside the gauge is a resistive network (an air-core movement's coils and resistors) fed from the
I terminal, and the sender runs from the S terminal to ground. Seen from the S terminal, any
resistive network is *exactly* equivalent to one voltage V_th behind one resistance R_th
(Thevenin's theorem), so

    V_S = V_th * R_s / (R_s + R_th)

That is a divider, and a divider is not linear in the sender's resistance. A straight line drawn
between an "empty" and a "full" reading is around 20 points out at half a tank -- and capturing
"empty" at all would mean running the tank dry.

The fit
-------
Dividing by the gauge's own supply makes the reading independent of whether the engine is charging
(V_th scales with the supply in an air-core gauge): r = V_S / V_I, and then

    1/r  =  a + b * (1/R_s)        where a = V_I / V_th and b = a * R_th

-- a straight line in (1/R_s, 1/r). A fuel sender's resistance is standardised (US: 240 ohm empty,
33 ohm full, linear in float travel), so every captured point ("just filled up", "the analog gauge
reads 1/2") comes with a known R_s, and **two points anywhere on the scale fix a and b** -- which
covers the whole range, near-empty included, where the low-fuel alarm and the Range field matter
most. With three or more points it is a least-squares fit, and the residual says how well the
gauge really behaves like this. Beyond MODEL_TOLERANCE_PCT (a gauge with an internal regulator, a
tap on the wrong terminal) it falls back to straight lines between the captured points instead:
exact at every point, approximate in between.

Trim and oil
------------
Neither has a standard sender range to lean on -- the published ranges for MerCruiser trim senders
disagree with each other -- so both use straight lines between captured points: fully down and
fully up for trim, pressures read off the analog gauge for oil. More points, better in between.

Engine temperature
------------------
A temperature sender is a thermistor, R_s = R_0 * exp(B * (1/T - 1/T_0)), T in kelvin. How many
ohms it has differs from one make to the next (and a dual-station sender has half a single one's),
but that scale folds into b above, exactly as it does for fuel, so only the curve's shape matters.
The shape is B, and published curves agree on it: 450 / 99 / 29.6 ohm at 100 / 175 / 250 F give
~4000 K; MerCruiser's 121-147 / 47-55 / 36-41 ohm at 140 / 194 / 212 F give ~3900 K. So

    1/r  =  a + c * x        where x = exp(-B * (1/T - 1/T_ref))

is again a straight line, and two points fix a and c -- engine cold (key on after it has sat: the
air or lake temperature) and warmed up. What that buys is the overheat end, which nobody
calibrates on purpose: straight lines through a cold and a warm point, extended, read an overheat
15-45 F low in simulation, where this reads within 5 F even for a sender whose B is 3700 K. A
set of points the published B can't fit (three or more, spread widely) gets its own B; one no B
can fit falls back to straight lines, and says so.

Faults are no data, never a number
----------------------------------
A tap wire that has fallen off reads 0 V, which the divider model would call a sender shorted to
ground: a full tank. So a level more than PLAUSIBLE_MARGIN_PCT outside 0-100 is reported as no
reading rather than clamped, as is a gauge with no supply -- ignition off, the analog gauge is dark
too. Oil has no such bounded scale, so for oil a tap reading almost nothing against a live supply
is taken as disconnected.
"""
import math

MIN_GAUGE_SUPPLY_V = 8.0      # below this the gauges are off (key off), or the supply tap is loose
PLAUSIBLE_MARGIN_PCT = 15.0   # this far past 0-100 is a fault, not a level to clamp
MIN_POINT_SPREAD_PCT = 25.0   # points closer together than this cannot pin down a curve
MODEL_TOLERANCE_PCT = 4.0     # worse than this and the divider model is abandoned for the table
MIN_OIL_TAP_FRACTION = 0.03   # an oil tap reading less than this share of the supply is unplugged
MAX_POINTS = 12
TEMP_BETA_K = 3950.0          # a temperature sender's B, between the published curves' 3900 and 4000
TEMP_BETA_FIT_K = (3300.0, 4700.0)   # the B a wide set of points may choose for itself
TEMP_REF_K = 355.0            # where x = 1: about 180 F, keeping x near 1 across the gauge
MIN_TEMP_SPREAD_F = 40.0      # cold and warmed up are 80-100 F apart
MIN_BETA_SPREAD_F = 60.0      # points must span this much before they may choose their own B
TEMP_TOLERANCE_F = 4.0        # worse than this at a point and the fit is not the gauge
TEMP_PLAUSIBLE_F = (20.0, 300.0)     # outside this is a fault (a tap wire off reads as boiling)


def tap_ratio(tap_v, supply_v, ratiometric=True):
    """The quantity every level is computed from: the sender voltage as a share of the gauge supply
    (or the bare voltage, for a gauge with its own regulator). None if it cannot be known."""
    if tap_v is None or not math.isfinite(tap_v):
        return None
    if not ratiometric:
        return tap_v
    if supply_v is None or not math.isfinite(supply_v) or supply_v < MIN_GAUGE_SUPPLY_V:
        return None
    return tap_v / supply_v


def clean_points(points):
    """Captured points from the calibration file as (ratio, value) pairs, anything malformed dropped."""
    out = []
    for p in points or []:
        try:
            ratio, value = float(p[0]), float(p[1])
        except (TypeError, ValueError, IndexError):
            continue
        if math.isfinite(ratio) and math.isfinite(value) and ratio > 0:
            out.append((ratio, value))
    return out


def add_point(points, ratio, value, same_within):
    """The point list with (ratio, value) added, replacing any earlier point at nearly the same value
    (capturing "full" again after the next fill-up updates it rather than piling up). Newest last."""
    kept = [p for p in clean_points(points) if abs(p[1] - value) > same_within]
    kept.append((round(ratio, 5), round(value, 2)))
    return [list(p) for p in kept[-MAX_POINTS:]]


def spread(points):
    values = [v for _, v in points]
    return max(values) - min(values) if values else 0.0


# ---------------- fuel: the divider model ----------------
def sender_ohms_at(pct, empty_ohm, full_ohm):
    """A float sender's resistance at a level: linear in float travel between its two end values."""
    return empty_ohm + (full_ohm - empty_ohm) * pct / 100.0


def fit_divider(points, empty_ohm, full_ohm):
    """(a, b) for 1/r = a + b/R_s by least squares over the points, or None if they cannot give a
    physical answer (fewer than two, all at one level, or a result that is no divider at all)."""
    xs, ys = [], []
    for ratio, pct in points:
        r_s = sender_ohms_at(pct, empty_ohm, full_ohm)
        if r_s <= 0:
            return None
        xs.append(1.0 / r_s)
        ys.append(1.0 / ratio)
    if len(xs) < 2:
        return None
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx == 0:
        return None
    b = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
    a = my - b * mx
    if a <= 0 or b <= 0:   # the voltage falls as the sender's resistance rises: not a sender divider
        return None
    return a, b


def divider_level(ratio, fit, empty_ohm, full_ohm):
    """Percent from a fit, not clamped (a fault shows up as far outside 0-100). None if the reading
    is at or past what an open sender would give."""
    if ratio is None or ratio <= 0:
        return None
    a, b = fit
    excess = 1.0 / ratio - a
    if excess <= 0:
        return None
    r_s = b / excess
    return (empty_ohm - r_s) / (empty_ohm - full_ohm) * 100.0


# ---------------- trim, oil, and the fuel fallback: straight lines between points ----------------
def table_value(ratio, points):
    """Straight lines between the points, extended past the ends along the end segments. None with
    fewer than two distinct readings."""
    if ratio is None:
        return None
    merged = {}
    for r, v in points:                       # two captures at the same reading: use their average
        merged.setdefault(r, []).append(v)
    pts = sorted((r, sum(vs) / len(vs)) for r, vs in merged.items())
    if len(pts) < 2:
        return None
    if ratio <= pts[0][0]:
        (r0, v0), (r1, v1) = pts[0], pts[1]
    elif ratio >= pts[-1][0]:
        (r0, v0), (r1, v1) = pts[-2], pts[-1]
    else:
        i = next(i for i in range(len(pts) - 1) if pts[i][0] <= ratio <= pts[i + 1][0])
        (r0, v0), (r1, v1) = pts[i], pts[i + 1]
    return v0 + (v1 - v0) * (ratio - r0) / (r1 - r0)


def bounded_percent(value):
    """0-100, or None if the value is far enough outside that it is a fault rather than a level."""
    if value is None or value < -PLAUSIBLE_MARGIN_PCT or value > 100 + PLAUSIBLE_MARGIN_PCT:
        return None
    return max(0.0, min(100.0, value))


# ---------------- engine temperature: the divider model with a thermistor sender ----------------
def _kelvin(temp_f):
    return (temp_f - 32.0) * 5.0 / 9.0 + 273.15


def _fahrenheit(kelvin):
    return (kelvin - 273.15) * 9.0 / 5.0 + 32.0


def thermistor_x(temp_f, beta):
    """1/R_s at this temperature, up to a constant the fit absorbs."""
    return math.exp(-beta * (1.0 / _kelvin(temp_f) - 1.0 / TEMP_REF_K))


def fit_thermistor(points, beta):
    """(a, c, beta) for 1/r = a + c*x by least squares, or None if the points give no divider."""
    if len(points) < 2:
        return None
    xs = [thermistor_x(t, beta) for _, t in points]
    ys = [1.0 / ratio for ratio, _ in points]
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx == 0:
        return None
    c = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
    a = my - c * mx
    if a <= 0 or c <= 0:   # hotter must mean a lower voltage, from a real supply
        return None
    return a, c, beta


def thermistor_temp(ratio, fit):
    """Degrees F from a fit, unbounded. None at or past an open sender (colder than it can say)."""
    if ratio is None or ratio <= 0:
        return None
    a, c, beta = fit
    excess = 1.0 / ratio - a
    if excess <= 0:
        return None
    inverse_k = 1.0 / TEMP_REF_K - math.log(excess / c) / beta
    return _fahrenheit(1.0 / inverse_k) if inverse_k > 0 else None


def _worst_miss(points, fit):
    misses = [thermistor_temp(r, fit) for r, _ in points]
    return max(math.inf if m is None else abs(m - t) for m, (_, t) in zip(misses, points))


def best_thermistor_fit(points):
    """(fit, worst miss in F) with the published B -- or, when that misses and the points span
    enough to say otherwise, with the B in TEMP_BETA_FIT_K that fits them best. (None, inf) if
    neither can."""
    fit = fit_thermistor(points, TEMP_BETA_K)
    miss = _worst_miss(points, fit) if fit else math.inf
    if miss <= TEMP_TOLERANCE_F or len(points) < 3 or spread(points) < MIN_BETA_SPREAD_F:
        return fit, miss
    lo, hi = TEMP_BETA_FIT_K
    for beta in (lo + i * 25.0 for i in range(int((hi - lo) / 25.0) + 1)):
        candidate = fit_thermistor(points, beta)
        candidate_miss = _worst_miss(points, candidate) if candidate else math.inf
        if candidate_miss < miss:
            fit, miss = candidate, candidate_miss
    return fit, miss


# ---------------- what the hub calls ----------------
def fuel_level(ratio, points, empty_ohm, full_ohm):
    """(percent or None, how: dict for the calibration page)."""
    pts = clean_points(points)
    how = {"points": len(pts), "method": None, "residual_pct": None, "note": None}
    if len(pts) < 2:
        how["note"] = "needs two points: 'just filled up', and one more when the analog gauge reads 1/2 or lower"
        return None, how
    if spread(pts) < MIN_POINT_SPREAD_PCT:
        how["note"] = f"points are too close together; add one at least {MIN_POINT_SPREAD_PCT:.0f}% away from the others"
        return None, how
    fit = fit_divider(pts, empty_ohm, full_ohm)
    if fit is not None:
        # A fit that cannot reproduce one of the captured points at all is as bad as a fit can be.
        fitted = [divider_level(r, fit, empty_ohm, full_ohm) for r, _ in pts]
        residual = max(math.inf if f is None else abs(f - p) for f, (_, p) in zip(fitted, pts))
        how["residual_pct"] = round(residual, 1) if math.isfinite(residual) else None
        if residual <= MODEL_TOLERANCE_PCT:
            how["method"] = "divider"
            return bounded_percent(divider_level(ratio, fit, empty_ohm, full_ohm)), how
        how["note"] = ("the gauge does not follow the divider model closely"
                       + (f" (off by up to {residual:.0f}%)" if math.isfinite(residual) else "")
                       + "; using straight lines between your points")
    else:
        how["note"] = "these points do not look like a sender divider (is the tap on the gauge's S terminal?); using straight lines between them"
    how["method"] = "table"
    return bounded_percent(table_value(ratio, pts)), how


def trim_level(ratio, points):
    pts = clean_points(points)
    how = {"points": len(pts), "method": "table" if len(pts) >= 2 else None, "note": None}
    if len(pts) < 2 or spread(pts) < MIN_POINT_SPREAD_PCT:
        how["method"] = None
        how["note"] = "save the drive fully DOWN and fully UP"
        return None, how
    return bounded_percent(table_value(ratio, pts)), how


def oil_pressure(ratio, points, ratiometric=True):
    pts = clean_points(points)
    how = {"points": len(pts), "method": "table" if len(pts) >= 2 else None, "note": None}
    if len(pts) < 2 or spread(pts) <= 0:
        how["method"] = None
        how["note"] = "save 0 psi (key on, engine off) and a reading off the analog gauge with the engine running"
        return None, how
    if ratio is None or (ratiometric and ratio < MIN_OIL_TAP_FRACTION):
        return None, how   # nothing on the tap against a live supply: the wire is off, not the pressure
    value = table_value(ratio, pts)
    top = max(v for _, v in pts)
    if value is None or value > 2 * top + 10:
        return None, how
    return max(0.0, value), how


def engine_temp(ratio, points):
    """(degrees F or None, how: dict for the calibration page)."""
    pts = clean_points(points)
    how = {"points": len(pts), "method": None, "residual_f": None, "beta_k": None, "note": None}
    if len(pts) < 2:
        how["note"] = ("needs two points: one with the engine cold (key on after it has sat a few hours: "
                       "type the air or lake temperature), and one warmed up")
        return None, how
    if spread(pts) < MIN_TEMP_SPREAD_F:
        how["note"] = f"points are too close together; add one at least {MIN_TEMP_SPREAD_F:.0f} F away from the others"
        return None, how
    fit, miss = best_thermistor_fit(pts)
    if fit is not None and miss <= TEMP_TOLERANCE_F:
        how.update(method="sender", residual_f=round(miss, 1), beta_k=round(fit[2]))
        value = thermistor_temp(ratio, fit)
    else:
        how["note"] = ("these points do not follow a temperature sender's curve"
                       + (f" (off by up to {miss:.0f} F)" if math.isfinite(miss) else "")
                       + "; using straight lines between them, which read an overheat low")
        how["method"] = "table"
        value = table_value(ratio, pts)
    lo, hi = TEMP_PLAUSIBLE_F
    if value is None or not lo <= value <= hi:
        return None, how
    return value, how
