import json
import storage
from pathlib import Path

def generate_config(output_report: str = "model_evaluator_report.md"):
    averages = storage.get_historical_averages()
    
    if not averages:
        print("No historical data to generate config.")
        return

    # Basic tier-based router calculation
    config = {
        "routing_policy": {
            "low": [],
            "medium": [],
            "high": []
        },
        "metrics_summary": {}
    }
    
    for row in averages:
        tier = row['tier']
        model = row['model']
        score = row['avg_score']
        cost = row['total_cost']
        
        if tier not in config["routing_policy"]:
            config["routing_policy"][tier] = []
            
        config["routing_policy"][tier].append({
            "model": model,
            "score": score,
            "cost": cost
        })
        
    # Sort models per tier (Best Score first, then cheapest)
    for tier in config["routing_policy"]:
        config["routing_policy"][tier].sort(key=lambda x: (-x['score'], x['cost']))
        # Strip down to just ordered strings for the fallback chain
        config["routing_policy"][tier] = [m['model'] for m in config["routing_policy"][tier]]

    config_path = Path("active_router_config.json")
    with open(config_path, "w") as f:
        json.dump(config, f, indent=2)
        
    print(f"Generated executable config at {config_path}")

    # Generate Human Report
    report_path = Path(output_report)
    with open(report_path, "w") as f:
        f.write("# Model Evaluator Report\n\n")
        f.write("## Fallback Chains (Best Quality/Value)\n")
        for tier, models in config["routing_policy"].items():
            f.write(f"### Tier: {tier.capitalize()}\n")
            for idx, model in enumerate(models):
                f.write(f"{idx+1}. `{model}`\n")
            f.write("\n")
            
    print(f"Generated human report at {report_path}")

if __name__ == "__main__":
    generate_config()
