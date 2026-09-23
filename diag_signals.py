import sys, json, csv, statistics
sys.path.append('.')
from agent.tg_store import TigerGraphMCPStore

gs = TigerGraphMCPStore(needed_customers=None)

cases = []
with open('data/case_pack.csv') as f:
    for row in csv.DictReader(f):
        cases.append(row)

# All 20 cases — we'll note which ones already matched specific patterns
already_matched = {'HHG-014'}  # card_not_present_new_device from live run; rest fallback

for c in cases:
    txn_id = c['flagged_txn_id']
    card_id = c['card_id']
    trigger = c['trigger_type']
    risk_score = c.get('risk_score', '')

    txn = gs.get_txn(txn_id)
    if txn is None:
        print(f"{c['case_id']}: TXN NOT FOUND")
        continue

    hist = gs.card_history(card_id)
    n_hist = len(hist['txns'])
    window1h = gs.card_window(card_id, txn_id, hours=1)
    window24h = gs.card_window(card_id, txn_id, hours=24)
    window48h = gs.card_window(card_id, txn_id, hours=48)
    dn = gs.device_neighbors(txn_id)
    idrow = gs.identity_by_txn.get(txn_id)

    amt = float(txn['TransactionAmt']) if txn.get('TransactionAmt') else 0
    channel = txn.get('channel', '')
    addr1 = txn.get('addr1', '')
    product = txn.get('ProductCD', '')

    # Card-testing signals
    small_1h = [t for t in window1h if t.get('channel') == 'online' and t.get('TransactionAmt') and float(t['TransactionAmt']) < 5]
    large_1h = [t for t in window1h if t.get('TransactionAmt') and float(t['TransactionAmt']) >= 100]

    # CNP signals
    online_48h = [t for t in window48h if t.get('channel') == 'online']

    # ATO signals
    bad_flags = [f for f in ('M1', 'M4', 'M6') if txn.get(f) == 'F']
    channels_24h = {t.get('channel') for t in window24h}

    # Baseline
    hist_amts = [float(t['TransactionAmt']) for t in hist['txns']
                 if t['TransactionID'] != txn_id and t.get('TransactionAmt')]
    median_amt = statistics.median(hist_amts) if hist_amts else None
    max_amt = max(hist_amts) if hist_amts else None
    hist_products = {t.get('ProductCD') for t in hist['txns'] if t['TransactionID'] != txn_id}
    hist_regions = {t.get('addr1') for t in hist['txns']
                    if t['TransactionID'] != txn_id and t.get('addr1')}

    unusual_amt = bool(median_amt and amt > 4 * median_amt and max_amt and amt > max_amt * 1.25)
    unusual_product = product not in hist_products if hist_products else False
    new_region = bool(addr1 and addr1 not in hist_regions) if hist_regions else False

    other_cards = dn.get('other_cards', []) if dn else []
    is_new_dev = False
    if idrow:
        is_new_dev = (idrow or {}).get('id_15', '') == 'New'

    # Recurring pattern check (simplified)
    from datetime import datetime
    anchor_ts = None
    try:
        anchor_ts = datetime.strptime(txn['ts'], '%Y-%m-%d %H:%M:%S')
    except Exception:
        pass
    recurring = []
    if anchor_ts:
        for t in hist['txns']:
            if t['TransactionID'] == txn_id:
                continue
            if t.get('ProductCD') != product:
                continue
            t_amt = float(t['TransactionAmt']) if t.get('TransactionAmt') else None
            if t_amt is None or abs(t_amt - amt) > 0.08 * amt:
                continue
            try:
                t_ts = datetime.strptime(t['ts'], '%Y-%m-%d %H:%M:%S')
                gap = abs((anchor_ts - t_ts).days)
                if 20 <= gap <= 40:
                    recurring.append(t['TransactionID'])
            except Exception:
                pass

    # CNP detector exact match (mirroring detect_cnp_fraud logic)
    cnp_fires = channel == 'online' and (unusual_amt or unusual_product)
    cnp_burst = len(online_48h) >= 3
    cnp_strength = 0.0
    if cnp_fires:
        cnp_strength = 0.30
        if cnp_burst:
            cnp_strength += 0.15

    # Out-of-region exact match
    oor_fires = channel == 'in_person' and bool(addr1) and new_region

    # ATO exact match
    ato_fires = bool(bad_flags) and (len(channels_24h) > 1 or bool(bad_flags))

    # Card testing exact match
    ct_fires = len(small_1h) >= 3 and bool(large_1h)

    # New device exact match
    nd_fires = channel == 'online' and bool(idrow) and is_new_dev

    already = c['case_id'] in already_matched
    print(f"{'[MATCHED]' if already else '[FALLBACK]'} {c['case_id']} trigger={trigger} risk_score={risk_score}")
    print(f"  txn: channel={channel} amt={amt:.2f} product={product} addr1={addr1}")
    print(f"  hist: n_hist={n_hist} median={median_amt} max={max_amt} n_products={len(hist_products)} n_regions={len(hist_regions)}")
    print(f"  recurring_hits={len(recurring)}")
    print(f"  --- Detector eligibility ---")
    print(f"  card_testing:   small_1h={len(small_1h)} large_1h={len(large_1h)} -> fires={ct_fires}")
    print(f"  new_device_cnp: channel={channel} idrow={'yes' if idrow else 'NO'} is_new_dev={is_new_dev} -> fires={nd_fires}")
    print(f"  cnp_fraud:      unusual_amt={unusual_amt} unusual_product={unusual_product} online_48h={len(online_48h)} -> fires={cnp_fires} strength={cnp_strength:.2f}")
    print(f"  out_of_region:  channel={channel} addr1={addr1!r} new_region={new_region} -> fires={oor_fires}")
    print(f"  account_takeo:  bad_flags={bad_flags} channels_24h={channels_24h} -> fires={ato_fires}")
    print()
