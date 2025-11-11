# fake_kpi_stream.py
import json
import random
import time
from datetime import datetime, timezone
from pathlib import Path

MAX_KPIS = 20
OUTPUT_FILE = Path("fake_kpi_stream.json")

def random_cell_metrics():
    return {
        "cell_id": "CELL_001",
        "prb_used_dl": random.randint(40, 90),
        "prb_used_ul": random.randint(30, 80),
        "prb_total": 100,
        "thr_dl_bps": random.uniform(20e6, 60e6),
        "thr_ul_bps": random.uniform(2e6, 10e6),
        "bler_dl": round(random.uniform(0.01, 0.15), 3),
        "bler_ul": round(random.uniform(0.01, 0.12), 3),
        "cqi_avg": round(random.uniform(5.0, 14.0), 2),
        "mcs_dl_avg": random.randint(10, 28),
        "mcs_ul_avg": random.randint(8, 25),
        "active_ue_count": random.randint(10, 35),
        "pdcp_pdu_tx": random.randint(5_000, 50_000),
        "pdcp_pdu_rx": random.randint(5_000, 50_000),
        "rlc_sdu_tx": random.randint(1_000, 10_000),
        "rlc_sdu_rx": random.randint(1_000, 10_000),
        "delay_p50_ms": round(random.uniform(10, 50), 2),
        "delay_p95_ms": round(random.uniform(20, 100), 2),
        "delay_p99_ms": round(random.uniform(30, 150), 2),
        "jitter_p95_ms": round(random.uniform(2, 10), 2),
        "handovers_triggered": random.randint(0, 3)
    }

def random_ue_metrics():
    metrics = []
    for i in range(random.randint(1, 5)):  # up to 5 UEs per snapshot
        metrics.append({
            "ue_id": f"UE_{i+1:03d}",
            "cell_id": "CELL_001",
            "rsrp_dbm": round(random.uniform(-100, -70), 2),
            "rsrq_db": round(random.uniform(-15, -5), 2),
            "sinr_db": round(random.uniform(5, 30), 2),
            "cqi_avg": round(random.uniform(6, 14), 2),
            "mcs_dl_avg": random.randint(10, 28),
            "mcs_ul_avg": random.randint(8, 25),
            "thr_dl_bps": random.uniform(1e6, 10e6),
            "thr_ul_bps": random.uniform(0.5e6, 5e6),
            "bler_dl": round(random.uniform(0.0, 0.1), 3),
            "bler_ul": round(random.uniform(0.0, 0.1), 3),
            "buffer_bytes_avg": round(random.uniform(1e3, 1e5), 2),
            "packet_delay_ms_p95": round(random.uniform(10, 100), 2),
            "handover_count": random.randint(0, 2),
        })
    return metrics

def random_header(seq):
    now = datetime.now(timezone.utc).isoformat()
    return {
        "ric_instance_id": "ric_001",
        "function_id": "kpm_func_v1",
        "kpm_version": "2.0",
        "granularity_period_ms": 1000,
        "window_start": now,
        "window_end": now,
        "sequence_number": seq
    }

def generate_kpi_snapshot(seq):
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "Header": random_header(seq),
        "CellMetrics": random_cell_metrics(),
        "UEMetrics": random_ue_metrics()
    }

def main():
    if OUTPUT_FILE.exists():
        with open(OUTPUT_FILE, "r") as f:
            data = json.load(f)
    else:
        data = {"kpi_stream": []}

    seq = len(data["kpi_stream"]) + 1

    while True:
        new_snapshot = generate_kpi_snapshot(seq)
        data["kpi_stream"].append(new_snapshot)

        # maintain a rolling window of 20 snapshots
        if len(data["kpi_stream"]) > MAX_KPIS:
            data["kpi_stream"].pop(0)

        with open(OUTPUT_FILE, "w") as f:
            json.dump(data, f, indent=2)

        print(f"[+] Added KPI snapshot #{seq} (total={len(data['kpi_stream'])})")
        seq += 1
        time.sleep(2)  # every 2 seconds

if __name__ == "__main__":
    main()
