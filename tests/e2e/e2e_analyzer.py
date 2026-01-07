#!/usr/bin/env python3
"""
ACM Switchover E2E Test Analysis and Reporting Tool

Analyzes E2E test results, generates comprehensive reports, and provides
insights into switchover performance and reliability.

Usage:
    python e2e_analyzer.py --results-dir ./e2e-results-20240101-120000
    python e2e_analyzer.py --results-dir ./e2e-results-* --compare ./baseline-run
"""

import argparse
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple

# Optional dependencies with graceful fallback
try:
    import pandas as pd
    PANDAS_AVAILABLE = True
except ImportError:
    pd = None
    PANDAS_AVAILABLE = False

try:
    import matplotlib.pyplot as plt
    import seaborn as sns
    MATPLOTLIB_AVAILABLE = True
except ImportError:
    plt = None
    sns = None
    MATPLOTLIB_AVAILABLE = False


def calculate_percentiles(values: List[float]) -> Dict[str, float]:
    """Calculate P50, P90, P95 percentiles for a list of values."""
    if not values:
        return {"p50": 0.0, "p90": 0.0, "p95": 0.0}
    
    sorted_values = sorted(values)
    n = len(sorted_values)
    
    def percentile(p: float) -> float:
        """Calculate the p-th percentile."""
        k = (n - 1) * p / 100.0
        f = int(k)
        c = f + 1 if f + 1 < n else f
        return sorted_values[f] + (k - f) * (sorted_values[c] - sorted_values[f])
    
    return {
        "p50": percentile(50),
        "p90": percentile(90),
        "p95": percentile(95),
    }


class E2ETestAnalyzer:
    """Analyzes E2E test results and generates comprehensive reports."""
    
    def __init__(self, results_dir: str):
        self.results_dir = Path(results_dir)
        self.cycles_data = {}
        self.metrics_data = []
        self.alerts_data = []
        self.summary_data = {}
        
    def load_test_data(self) -> bool:
        """Load all test data from results directory."""
        print(f"Loading test data from: {self.results_dir}")
        
        if not self.results_dir.exists():
            print(f"Error: Results directory {self.results_dir} does not exist")
            return False
            
        # Load cycle results
        cycle_results_file = self.results_dir / "cycle_results.csv"
        if cycle_results_file.exists():
            self.cycles_data = self._load_cycle_results(cycle_results_file)
        else:
            print("Warning: cycle_results.csv not found")
            
        # Load metrics
        metrics_dir = self.results_dir / "metrics"
        if metrics_dir.exists():
            self.metrics_data = self._load_metrics(metrics_dir)
            
        # Load alerts
        alerts_dir = self.results_dir / "alerts"
        if alerts_dir.exists():
            self.alerts_data = self._load_alerts(alerts_dir)
            
        # Load summary
        summary_file = self.results_dir / "summary_report.txt"
        if summary_file.exists():
            self.summary_data = self._load_summary(summary_file)
            
        print(f"Loaded data for {len(self.cycles_data)} cycles")
        print(f"Loaded {len(self.metrics_data)} metric samples")
        print(f"Loaded {len(self.alerts_data)} alerts")
        
        return True
        
    def _load_cycle_results(self, file_path: Path) -> Dict[int, Dict[str, Any]]:
        """Load cycle results from CSV file."""
        cycles = {}
        
        try:
            if PANDAS_AVAILABLE:
                df = pd.read_csv(file_path)
                for _, row in df.iterrows():
                    cycle = int(row['cycle'].replace('cycle_', '')) if isinstance(row['cycle'], str) else int(row['cycle'])
                    if cycle not in cycles:
                        cycles[cycle] = {}
                        
                    cycles[cycle][row['phase']] = {
                        'status': 'SUCCESS' if row['exit_code'] == 0 else 'FAILED',
                        'duration_seconds': row['duration_seconds'],
                        'start_time': row['start_time'],
                        'end_time': row['end_time'],
                        'exit_code': row['exit_code']
                    }
            else:
                # Fallback: manual CSV parsing
                import csv
                with open(file_path, 'r') as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        cycle_str = row['cycle']
                        cycle = int(cycle_str.replace('cycle_', '')) if 'cycle_' in cycle_str else int(cycle_str)
                        if cycle not in cycles:
                            cycles[cycle] = {}
                            
                        exit_code = int(row['exit_code'])
                        cycles[cycle][row['phase']] = {
                            'status': 'SUCCESS' if exit_code == 0 else 'FAILED',
                            'duration_seconds': float(row['duration_seconds']),
                            'start_time': row['start_time'],
                            'end_time': row['end_time'],
                            'exit_code': exit_code
                        }
                
        except Exception as e:
            print(f"Error loading cycle results: {e}")
            
        return cycles
        
    def _load_metrics(self, metrics_dir: Path) -> List[Dict[str, Any]]:
        """Load all metrics JSON files."""
        metrics = []
        
        for metric_file in metrics_dir.glob("metrics_*.json"):
            try:
                with open(metric_file, 'r') as f:
                    data = json.load(f)
                    metrics.append(data)
            except Exception as e:
                print(f"Error loading {metric_file}: {e}")
                
        return sorted(metrics, key=lambda x: x.get('timestamp', 0))
        
    def _load_alerts(self, alerts_dir: Path) -> List[Dict[str, Any]]:
        """Load all alert JSON files."""
        alerts = []
        
        for alert_file in alerts_dir.glob("*.json"):
            try:
                with open(alert_file, 'r') as f:
                    data = json.load(f)
                    data['file_name'] = alert_file.name
                    alerts.append(data)
            except Exception as e:
                print(f"Error loading {alert_file}: {e}")
                
        return alerts
        
    def _load_summary(self, summary_file: Path) -> Dict[str, Any]:
        """Load summary report data."""
        summary = {}
        
        try:
            with open(summary_file, 'r') as f:
                content = f.read()
                
            # Parse key information from summary
            for line in content.split('\n'):
                if ':' in line:
                    key, value = line.split(':', 1)
                    summary[key.strip()] = value.strip()
                    
        except Exception as e:
            print(f"Error loading summary: {e}")
            
        return summary
        
    def analyze_performance(self) -> Dict[str, Any]:
        """Analyze performance metrics across all cycles."""
        print("Analyzing performance metrics...")
        
        performance = {
            'cycle_performance': {},
            'phase_performance': {},
            'overall_metrics': {}
        }
        
        # Analyze each cycle
        for cycle, phases in self.cycles_data.items():
            cycle_perf = {
                'total_duration': 0,
                'successful_phases': 0,
                'failed_phases': 0,
                'phases': phases
            }
            
            for phase, data in phases.items():
                cycle_perf['total_duration'] += data['duration_seconds']
                if data['status'] == 'SUCCESS':
                    cycle_perf['successful_phases'] += 1
                else:
                    cycle_perf['failed_phases'] += 1
                    
            performance['cycle_performance'][cycle] = cycle_perf
            
        # Analyze phase performance across cycles
        phase_stats = {}
        for cycle, phases in self.cycles_data.items():
            for phase, data in phases.items():
                if phase not in phase_stats:
                    phase_stats[phase] = {
                        'durations': [],
                        'success_count': 0,
                        'failure_count': 0
                    }
                    
                phase_stats[phase]['durations'].append(data['duration_seconds'])
                if data['status'] == 'SUCCESS':
                    phase_stats[phase]['success_count'] += 1
                else:
                    phase_stats[phase]['failure_count'] += 1
                    
        # Calculate statistics for each phase
        for phase, stats in phase_stats.items():
            durations = stats['durations']
            percentiles = calculate_percentiles(durations)
            total_executions = stats['success_count'] + stats['failure_count']
            performance['phase_performance'][phase] = {
                'avg_duration': sum(durations) / len(durations) if durations else 0,
                'min_duration': min(durations) if durations else 0,
                'max_duration': max(durations) if durations else 0,
                'p50_duration': percentiles['p50'],
                'p90_duration': percentiles['p90'],
                'p95_duration': percentiles['p95'],
                'success_rate': (stats['success_count'] / total_executions * 100) if total_executions > 0 else 0,
                'total_executions': total_executions
            }
            
        # Overall metrics
        total_cycles = len(self.cycles_data)
        successful_cycles = sum(1 for cycle, perf in performance['cycle_performance'].items() 
                               if perf['failed_phases'] == 0)
        
        performance['overall_metrics'] = {
            'total_cycles': total_cycles,
            'successful_cycles': successful_cycles,
            'failed_cycles': total_cycles - successful_cycles,
            'overall_success_rate': successful_cycles / total_cycles * 100 if total_cycles > 0 else 0,
            'avg_cycle_duration': sum(perf['total_duration'] for perf in performance['cycle_performance'].values()) / total_cycles if total_cycles > 0 else 0
        }
        
        return performance
        
    def analyze_alerts(self) -> Dict[str, Any]:
        """Analyze alert patterns and frequencies."""
        print("Analyzing alerts...")
        
        alert_analysis = {
            'total_alerts': len(self.alerts_data),
            'alert_types': {},
            'hub_distribution': {},
            'phase_distribution': {},
            'timeline': []
        }
        
        for alert in self.alerts_data:
            alert_type = alert.get('alert_type', 'UNKNOWN')
            hub_type = alert.get('hub_type', 'unknown')
            phase = alert.get('phase', 'unknown')
            timestamp = alert.get('timestamp', '')
            
            # Count by type
            if alert_type not in alert_analysis['alert_types']:
                alert_analysis['alert_types'][alert_type] = 0
            alert_analysis['alert_types'][alert_type] += 1
            
            # Count by hub
            if hub_type not in alert_analysis['hub_distribution']:
                alert_analysis['hub_distribution'][hub_type] = 0
            alert_analysis['hub_distribution'][hub_type] += 1
            
            # Count by phase
            if phase not in alert_analysis['phase_distribution']:
                alert_analysis['phase_distribution'][phase] = 0
            alert_analysis['phase_distribution'][phase] += 1
            
            # Add to timeline
            if timestamp:
                try:
                    dt = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
                    alert_analysis['timeline'].append({
                        'timestamp': dt,
                        'type': alert_type,
                        'hub': hub_type,
                        'message': alert.get('message', '')
                    })
                except ValueError:
                    pass
                    
        # Sort timeline
        alert_analysis['timeline'].sort(key=lambda x: x['timestamp'])
        
        return alert_analysis
        
    def analyze_resource_trends(self) -> Dict[str, Any]:
        """Analyze resource usage and trends over time."""
        print("Analyzing resource trends...")
        
        if not self.metrics_data:
            return {'error': 'No metrics data available'}
            
        trends = {
            'managed_clusters': {'primary': [], 'secondary': []},
            'backup_restore_status': [],
            'observability_deployments': {'primary': [], 'secondary': []},
            'timeline': []
        }
        
        for metric in self.metrics_data:
            timestamp = metric.get('timestamp', 0)
            iso_timestamp = metric.get('iso_timestamp', '')
            
            if not timestamp:
                continue
                
            trends['timeline'].append({
                'timestamp': timestamp,
                'iso_timestamp': iso_timestamp
            })
            
            # Managed cluster trends
            for hub_type in ['primary', 'secondary']:
                if hub_type in metric:
                    hub_data = metric[hub_type]
                    trends['managed_clusters'][hub_type].append({
                        'timestamp': timestamp,
                        'total': hub_data.get('total_managed_clusters', 0),
                        'available': hub_data.get('available_managed_clusters', 0)
                    })
                    
                    # Observability trends
                    trends['observability_deployments'][hub_type].append({
                        'timestamp': timestamp,
                        'deployments': hub_data.get('observability_deployments', 0),
                        'statefulsets': hub_data.get('observability_statefulsets', 0)
                    })
                    
            # Backup/restore status
            if 'primary' in metric and 'secondary' in metric:
                trends['backup_restore_status'].append({
                    'timestamp': timestamp,
                    'backup_phase': metric['primary'].get('backup_phase', 'unknown'),
                    'restore_phase': metric['secondary'].get('latest_restore_phase', 'unknown'),
                    'restore_count': metric['secondary'].get('restore_count', 0)
                })
                
        return trends
        
    def generate_html_report(self, output_file: str) -> bool:
        """Generate comprehensive HTML report."""
        print(f"Generating HTML report: {output_file}")
        
        # Analyze data
        performance = self.analyze_performance()
        alerts = self.analyze_alerts()
        trends = self.analyze_resource_trends()
        
        html_content = self._generate_html_template(performance, alerts, trends)
        
        try:
            with open(output_file, 'w') as f:
                f.write(html_content)
            print(f"HTML report generated: {output_file}")
            return True
        except Exception as e:
            print(f"Error generating HTML report: {e}")
            return False
            
    def _generate_html_template(self, performance: Dict, alerts: Dict, trends: Dict) -> str:
        """Generate HTML report template."""
        
        html = f"""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>ACM Switchover E2E Test Report</title>
    <style>
        body {{ font-family: Arial, sans-serif; margin: 20px; background-color: #f5f5f5; }}
        .container {{ max-width: 1200px; margin: 0 auto; background-color: white; padding: 20px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }}
        .header {{ text-align: center; margin-bottom: 30px; }}
        .section {{ margin-bottom: 30px; }}
        .metric-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(250px, 1fr)); gap: 20px; margin-bottom: 20px; }}
        .metric-card {{ background-color: #f8f9fa; padding: 20px; border-radius: 8px; border-left: 4px solid #007bff; }}
        .metric-value {{ font-size: 2em; font-weight: bold; color: #007bff; }}
        .metric-label {{ color: #666; margin-top: 5px; }}
        .success {{ color: #28a745; }}
        .warning {{ color: #ffc107; }}
        .error {{ color: #dc3545; }}
        table {{ width: 100%; border-collapse: collapse; margin-bottom: 20px; }}
        th, td {{ padding: 12px; text-align: left; border-bottom: 1px solid #ddd; }}
        th {{ background-color: #f8f9fa; font-weight: bold; }}
        .status-success {{ background-color: #d4edda; }}
        .status-failed {{ background-color: #f8d7da; }}
        .chart-placeholder {{ background-color: #f8f9fa; padding: 40px; text-align: center; border-radius: 4px; margin: 20px 0; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>ACM Switchover E2E Test Report</h1>
            <p>Generated on {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
            <p>Results directory: {self.results_dir}</p>
        </div>
        
        <div class="section">
            <h2>Executive Summary</h2>
            <div class="metric-grid">
                <div class="metric-card">
                    <div class="metric-value {('success' if performance['overall_metrics']['overall_success_rate'] >= 90 else 'warning' if performance['overall_metrics']['overall_success_rate'] >= 70 else 'error')}">
                        {performance['overall_metrics']['overall_success_rate']:.1f}%
                    </div>
                    <div class="metric-label">Overall Success Rate</div>
                </div>
                <div class="metric-card">
                    <div class="metric-value success">
                        {performance['overall_metrics']['successful_cycles']}/{performance['overall_metrics']['total_cycles']}
                    </div>
                    <div class="metric-label">Successful Cycles</div>
                </div>
                <div class="metric-card">
                    <div class="metric-value">
                        {performance['overall_metrics']['avg_cycle_duration']:.1f}s
                    </div>
                    <div class="metric-label">Average Cycle Duration</div>
                </div>
                <div class="metric-card">
                    <div class="metric-value {'error' if alerts['total_alerts'] > 0 else 'success'}">
                        {alerts['total_alerts']}
                    </div>
                    <div class="metric-label">Total Alerts</div>
                </div>
            </div>
        </div>
        
        <div class="section">
            <h2>Cycle Performance</h2>
            <table>
                <thead>
                    <tr>
                        <th>Cycle</th>
                        <th>Status</th>
                        <th>Duration</th>
                        <th>Successful Phases</th>
                        <th>Failed Phases</th>
                    </tr>
                </thead>
                <tbody>
"""
        
        # Add cycle performance rows
        for cycle, perf in performance['cycle_performance'].items():
            status_class = "status-success" if perf['failed_phases'] == 0 else "status-failed"
            status_text = "SUCCESS" if perf['failed_phases'] == 0 else "FAILED"
            
            html += f"""
                    <tr class="{status_class}">
                        <td>{cycle}</td>
                        <td>{status_text}</td>
                        <td>{perf['total_duration']}s</td>
                        <td>{perf['successful_phases']}</td>
                        <td>{perf['failed_phases']}</td>
                    </tr>
"""
        
        html += """
                </tbody>
            </table>
        </div>
        
        <div class="section">
            <h2>Phase Performance Analysis</h2>
            <table>
                <thead>
                    <tr>
                        <th>Phase</th>
                        <th>Avg Duration</th>
                        <th>P50</th>
                        <th>P90</th>
                        <th>P95</th>
                        <th>Min</th>
                        <th>Max</th>
                        <th>Success Rate</th>
                        <th>Executions</th>
                    </tr>
                </thead>
                <tbody>
"""
        
        # Add phase performance rows
        for phase, stats in performance['phase_performance'].items():
            success_rate_class = "success" if stats['success_rate'] >= 90 else "warning" if stats['success_rate'] >= 70 else "error"
            
            html += f"""
                    <tr>
                        <td>{phase}</td>
                        <td>{stats['avg_duration']:.1f}s</td>
                        <td>{stats['p50_duration']:.1f}s</td>
                        <td>{stats['p90_duration']:.1f}s</td>
                        <td>{stats['p95_duration']:.1f}s</td>
                        <td>{stats['min_duration']:.1f}s</td>
                        <td>{stats['max_duration']:.1f}s</td>
                        <td class="{success_rate_class}">{stats['success_rate']:.1f}%</td>
                        <td>{stats['total_executions']}</td>
                    </tr>
"""
        
        html += """
                </tbody>
            </table>
        </div>
        
        <div class="section">
            <h2>Alert Analysis</h2>
            <div class="metric-grid">
"""
        
        # Add alert metrics
        for alert_type, count in alerts['alert_types'].items():
            alert_class = "error" if count > 0 else "success"
            html += f"""
                <div class="metric-card">
                    <div class="metric-value {alert_class}">{count}</div>
                    <div class="metric-label">{alert_type.replace('_', ' ').title()}</div>
                </div>
"""
        
        html += """
            </div>
        </div>
        
        <div class="section">
            <h2>Recommendations</h2>
            <div class="metric-grid">
"""
        
        # Generate recommendations
        recommendations = self._generate_recommendations(performance, alerts, trends)
        for i, rec in enumerate(recommendations, 1):
            priority_class = rec['priority'].lower()
            html += f"""
                <div class="metric-card">
                    <div class="metric-label"><strong>{i}. {rec['title']}</strong> ({rec['priority']})</div>
                    <div>{rec['description']}</div>
                </div>
"""
        
        html += """
            </div>
        </div>
    </div>
</body>
</html>
"""
        
        return html
        
    def _generate_recommendations(self, performance: Dict, alerts: Dict, trends: Dict) -> List[Dict[str, str]]:
        """Generate recommendations based on analysis."""
        recommendations = []
        
        # Success rate recommendations
        success_rate = performance['overall_metrics']['overall_success_rate']
        if success_rate < 90:
            recommendations.append({
                'priority': 'HIGH' if success_rate < 70 else 'MEDIUM',
                'title': 'Improve Success Rate',
                'description': f'Overall success rate is {success_rate:.1f}%. Investigate failed cycles and address root causes.'
            })
            
        # Performance recommendations
        avg_duration = performance['overall_metrics']['avg_cycle_duration']
        if avg_duration > 2700:  # 45 minutes
            recommendations.append({
                'priority': 'MEDIUM',
                'title': 'Optimize Performance',
                'description': f'Average cycle duration is {avg_duration/60:.1f} minutes. Consider optimizing slow phases.'
            })
            
        # Alert recommendations
        if alerts['total_alerts'] > 0:
            recommendations.append({
                'priority': 'HIGH',
                'title': 'Address Critical Alerts',
                'description': f'{alerts["total_alerts"]} alerts generated. Review alert patterns and fix underlying issues.'
            })
            
        # Phase-specific recommendations
        for phase, stats in performance['phase_performance'].items():
            if stats['success_rate'] < 100:
                recommendations.append({
                    'priority': 'HIGH' if stats['success_rate'] < 80 else 'MEDIUM',
                    'title': f'Fix {phase.title()} Phase',
                    'description': f'{phase} phase has {stats["success_rate"]:.1f}% success rate. Investigate failures.'
                })
                
        return recommendations
        
    def generate_comparison_report(self, baseline_dir: str, output_file: str = "./e2e_comparison_report.html",
                                     regression_threshold: float = 1.2) -> bool:
        """Generate comparison report between current run and a baseline run.
        
        Args:
            baseline_dir: Path to the baseline results directory
            output_file: Output HTML file path
            regression_threshold: Multiplier for detecting regressions (1.2 = 20% slower)
            
        Returns:
            True if comparison report was generated successfully
        """
        print(f"Generating comparison report against baseline: {baseline_dir}")
        
        # Load baseline data
        baseline_analyzer = E2ETestAnalyzer(baseline_dir)
        if not baseline_analyzer.load_test_data():
            print(f"Failed to load baseline data from {baseline_dir}")
            return False
            
        # Analyze both runs
        current_perf = self.analyze_performance()
        baseline_perf = baseline_analyzer.analyze_performance()
        
        # Build comparison data
        comparison = self._compare_runs(current_perf, baseline_perf, regression_threshold)
        
        # Generate HTML comparison report
        html_content = self._generate_comparison_html(comparison, baseline_dir, regression_threshold)
        
        try:
            with open(output_file, 'w') as f:
                f.write(html_content)
            print(f"Comparison report generated: {output_file}")
            return True
        except Exception as e:
            print(f"Error generating comparison report: {e}")
            return False
            
    def _compare_runs(self, current: Dict, baseline: Dict, threshold: float) -> Dict[str, Any]:
        """Compare current run against baseline and flag regressions."""
        comparison = {
            'overall': {},
            'phases': {},
            'regressions': [],
            'improvements': []
        }
        
        # Compare overall metrics
        current_overall = current['overall_metrics']
        baseline_overall = baseline['overall_metrics']
        
        comparison['overall'] = {
            'current_success_rate': current_overall['overall_success_rate'],
            'baseline_success_rate': baseline_overall['overall_success_rate'],
            'success_rate_delta': current_overall['overall_success_rate'] - baseline_overall['overall_success_rate'],
            'current_avg_duration': current_overall['avg_cycle_duration'],
            'baseline_avg_duration': baseline_overall['avg_cycle_duration'],
            'duration_delta': current_overall['avg_cycle_duration'] - baseline_overall['avg_cycle_duration'],
            'duration_ratio': current_overall['avg_cycle_duration'] / baseline_overall['avg_cycle_duration'] if baseline_overall['avg_cycle_duration'] > 0 else 1.0
        }
        
        # Check for overall regressions
        if comparison['overall']['duration_ratio'] > threshold:
            comparison['regressions'].append({
                'type': 'overall',
                'metric': 'avg_cycle_duration',
                'current': current_overall['avg_cycle_duration'],
                'baseline': baseline_overall['avg_cycle_duration'],
                'ratio': comparison['overall']['duration_ratio'],
                'message': f"Overall cycle duration regressed by {(comparison['overall']['duration_ratio'] - 1) * 100:.1f}%"
            })
            
        if comparison['overall']['success_rate_delta'] < -5:  # 5% drop
            comparison['regressions'].append({
                'type': 'overall',
                'metric': 'success_rate',
                'current': current_overall['overall_success_rate'],
                'baseline': baseline_overall['overall_success_rate'],
                'ratio': current_overall['overall_success_rate'] / baseline_overall['overall_success_rate'] if baseline_overall['overall_success_rate'] > 0 else 0,
                'message': f"Success rate dropped by {abs(comparison['overall']['success_rate_delta']):.1f}%"
            })
        
        # Compare phase-level metrics
        current_phases = current['phase_performance']
        baseline_phases = baseline['phase_performance']
        
        all_phases = set(current_phases.keys()) | set(baseline_phases.keys())
        
        for phase in all_phases:
            current_phase = current_phases.get(phase, {})
            baseline_phase = baseline_phases.get(phase, {})
            
            phase_comparison = {
                'phase': phase,
                'in_current': phase in current_phases,
                'in_baseline': phase in baseline_phases
            }
            
            if current_phase and baseline_phase:
                # Calculate deltas for each metric
                for metric in ['avg_duration', 'p50_duration', 'p90_duration', 'p95_duration']:
                    current_val = current_phase.get(metric, 0)
                    baseline_val = baseline_phase.get(metric, 0)
                    ratio = current_val / baseline_val if baseline_val > 0 else 1.0
                    
                    phase_comparison[f'{metric}_current'] = current_val
                    phase_comparison[f'{metric}_baseline'] = baseline_val
                    phase_comparison[f'{metric}_delta'] = current_val - baseline_val
                    phase_comparison[f'{metric}_ratio'] = ratio
                    
                    # Flag regressions for P95
                    if metric == 'p95_duration' and ratio > threshold:
                        comparison['regressions'].append({
                            'type': 'phase',
                            'phase': phase,
                            'metric': metric,
                            'current': current_val,
                            'baseline': baseline_val,
                            'ratio': ratio,
                            'message': f"{phase} P95 duration regressed by {(ratio - 1) * 100:.1f}%"
                        })
                    elif metric == 'p95_duration' and ratio < 1 / threshold:
                        comparison['improvements'].append({
                            'type': 'phase',
                            'phase': phase,
                            'metric': metric,
                            'current': current_val,
                            'baseline': baseline_val,
                            'ratio': ratio,
                            'message': f"{phase} P95 duration improved by {(1 - ratio) * 100:.1f}%"
                        })
                        
                # Success rate comparison
                current_sr = current_phase.get('success_rate', 0)
                baseline_sr = baseline_phase.get('success_rate', 0)
                phase_comparison['success_rate_current'] = current_sr
                phase_comparison['success_rate_baseline'] = baseline_sr
                phase_comparison['success_rate_delta'] = current_sr - baseline_sr
                
                if current_sr < baseline_sr - 5:  # 5% drop
                    comparison['regressions'].append({
                        'type': 'phase',
                        'phase': phase,
                        'metric': 'success_rate',
                        'current': current_sr,
                        'baseline': baseline_sr,
                        'ratio': current_sr / baseline_sr if baseline_sr > 0 else 0,
                        'message': f"{phase} success rate dropped by {baseline_sr - current_sr:.1f}%"
                    })
                    
            comparison['phases'][phase] = phase_comparison
            
        return comparison
        
    def _generate_comparison_html(self, comparison: Dict, baseline_dir: str, threshold: float) -> str:
        """Generate HTML comparison report."""
        regressions_count = len(comparison['regressions'])
        improvements_count = len(comparison['improvements'])
        
        html = f"""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>ACM Switchover E2E Comparison Report</title>
    <style>
        body {{ font-family: Arial, sans-serif; margin: 20px; background-color: #f5f5f5; }}
        .container {{ max-width: 1400px; margin: 0 auto; background-color: white; padding: 20px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }}
        .header {{ text-align: center; margin-bottom: 30px; }}
        .section {{ margin-bottom: 30px; }}
        .metric-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(250px, 1fr)); gap: 20px; margin-bottom: 20px; }}
        .metric-card {{ background-color: #f8f9fa; padding: 20px; border-radius: 8px; border-left: 4px solid #007bff; }}
        .metric-value {{ font-size: 2em; font-weight: bold; color: #007bff; }}
        .metric-label {{ color: #666; margin-top: 5px; }}
        .success {{ color: #28a745; }}
        .warning {{ color: #ffc107; }}
        .error {{ color: #dc3545; }}
        .regression {{ background-color: #f8d7da; border-left-color: #dc3545; }}
        .improvement {{ background-color: #d4edda; border-left-color: #28a745; }}
        table {{ width: 100%; border-collapse: collapse; margin-bottom: 20px; }}
        th, td {{ padding: 12px; text-align: left; border-bottom: 1px solid #ddd; }}
        th {{ background-color: #f8f9fa; font-weight: bold; }}
        .delta-positive {{ color: #dc3545; }}
        .delta-negative {{ color: #28a745; }}
        .status-regression {{ background-color: #f8d7da; }}
        .status-improvement {{ background-color: #d4edda; }}
        .alert-box {{ padding: 15px; border-radius: 8px; margin-bottom: 20px; }}
        .alert-danger {{ background-color: #f8d7da; border: 1px solid #f5c6cb; color: #721c24; }}
        .alert-success {{ background-color: #d4edda; border: 1px solid #c3e6cb; color: #155724; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>ACM Switchover E2E Comparison Report</h1>
            <p>Generated on {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
            <p>Current run: {self.results_dir}</p>
            <p>Baseline: {baseline_dir}</p>
            <p>Regression threshold: {(threshold - 1) * 100:.0f}% slower</p>
        </div>
"""
        
        # Summary section
        if regressions_count > 0:
            html += f"""
        <div class="alert-box alert-danger">
            <strong>⚠️ {regressions_count} Regression(s) Detected!</strong> Performance or reliability has degraded compared to baseline.
        </div>
"""
        else:
            html += """
        <div class="alert-box alert-success">
            <strong>✓ No Regressions Detected</strong> Performance is within acceptable thresholds.
        </div>
"""
        
        # Overall comparison
        overall = comparison['overall']
        duration_class = 'error' if overall['duration_ratio'] > threshold else 'success' if overall['duration_ratio'] < 1 else ''
        sr_class = 'error' if overall['success_rate_delta'] < -5 else 'success' if overall['success_rate_delta'] > 5 else ''
        
        html += f"""
        <div class="section">
            <h2>Overall Comparison</h2>
            <div class="metric-grid">
                <div class="metric-card">
                    <div class="metric-value {sr_class}">{overall['current_success_rate']:.1f}%</div>
                    <div class="metric-label">Current Success Rate (baseline: {overall['baseline_success_rate']:.1f}%, delta: {overall['success_rate_delta']:+.1f}%)</div>
                </div>
                <div class="metric-card">
                    <div class="metric-value {duration_class}">{overall['current_avg_duration']:.1f}s</div>
                    <div class="metric-label">Current Avg Duration (baseline: {overall['baseline_avg_duration']:.1f}s, ratio: {overall['duration_ratio']:.2f}x)</div>
                </div>
                <div class="metric-card {'regression' if regressions_count > 0 else ''}">
                    <div class="metric-value error">{regressions_count}</div>
                    <div class="metric-label">Regressions Detected</div>
                </div>
                <div class="metric-card {'improvement' if improvements_count > 0 else ''}">
                    <div class="metric-value success">{improvements_count}</div>
                    <div class="metric-label">Improvements Detected</div>
                </div>
            </div>
        </div>
"""
        
        # Regressions detail
        if comparison['regressions']:
            html += """
        <div class="section">
            <h2>⚠️ Regressions</h2>
            <table>
                <thead>
                    <tr>
                        <th>Type</th>
                        <th>Phase/Metric</th>
                        <th>Current</th>
                        <th>Baseline</th>
                        <th>Ratio</th>
                        <th>Message</th>
                    </tr>
                </thead>
                <tbody>
"""
            for reg in comparison['regressions']:
                phase_str = reg.get('phase', 'Overall')
                html += f"""
                    <tr class="status-regression">
                        <td>{reg['type'].title()}</td>
                        <td>{phase_str} / {reg['metric']}</td>
                        <td>{reg['current']:.1f}</td>
                        <td>{reg['baseline']:.1f}</td>
                        <td class="delta-positive">{reg['ratio']:.2f}x</td>
                        <td>{reg['message']}</td>
                    </tr>
"""
            html += """
                </tbody>
            </table>
        </div>
"""
        
        # Improvements detail
        if comparison['improvements']:
            html += """
        <div class="section">
            <h2>✓ Improvements</h2>
            <table>
                <thead>
                    <tr>
                        <th>Type</th>
                        <th>Phase/Metric</th>
                        <th>Current</th>
                        <th>Baseline</th>
                        <th>Ratio</th>
                        <th>Message</th>
                    </tr>
                </thead>
                <tbody>
"""
            for imp in comparison['improvements']:
                phase_str = imp.get('phase', 'Overall')
                html += f"""
                    <tr class="status-improvement">
                        <td>{imp['type'].title()}</td>
                        <td>{phase_str} / {imp['metric']}</td>
                        <td>{imp['current']:.1f}</td>
                        <td>{imp['baseline']:.1f}</td>
                        <td class="delta-negative">{imp['ratio']:.2f}x</td>
                        <td>{imp['message']}</td>
                    </tr>
"""
            html += """
                </tbody>
            </table>
        </div>
"""
        
        # Phase comparison table
        html += """
        <div class="section">
            <h2>Phase-by-Phase Comparison</h2>
            <table>
                <thead>
                    <tr>
                        <th>Phase</th>
                        <th>P95 Current</th>
                        <th>P95 Baseline</th>
                        <th>P95 Ratio</th>
                        <th>Avg Current</th>
                        <th>Avg Baseline</th>
                        <th>SR Current</th>
                        <th>SR Baseline</th>
                        <th>Status</th>
                    </tr>
                </thead>
                <tbody>
"""
        
        for phase, data in comparison['phases'].items():
            if not data.get('in_current') or not data.get('in_baseline'):
                status = "N/A"
                status_class = ""
            else:
                p95_ratio = data.get('p95_duration_ratio', 1.0)
                sr_delta = data.get('success_rate_delta', 0)
                
                if p95_ratio > threshold or sr_delta < -5:
                    status = "REGRESSION"
                    status_class = "status-regression"
                elif p95_ratio < 1 / threshold or sr_delta > 5:
                    status = "IMPROVED"
                    status_class = "status-improvement"
                else:
                    status = "OK"
                    status_class = ""
                    
            p95_current = data.get('p95_duration_current', 0)
            p95_baseline = data.get('p95_duration_baseline', 0)
            p95_ratio = data.get('p95_duration_ratio', 1.0)
            avg_current = data.get('avg_duration_current', 0)
            avg_baseline = data.get('avg_duration_baseline', 0)
            sr_current = data.get('success_rate_current', 0)
            sr_baseline = data.get('success_rate_baseline', 0)
            
            ratio_class = 'delta-positive' if p95_ratio > threshold else 'delta-negative' if p95_ratio < 1/threshold else ''
            
            html += f"""
                    <tr class="{status_class}">
                        <td>{phase}</td>
                        <td>{p95_current:.1f}s</td>
                        <td>{p95_baseline:.1f}s</td>
                        <td class="{ratio_class}">{p95_ratio:.2f}x</td>
                        <td>{avg_current:.1f}s</td>
                        <td>{avg_baseline:.1f}s</td>
                        <td>{sr_current:.1f}%</td>
                        <td>{sr_baseline:.1f}%</td>
                        <td><strong>{status}</strong></td>
                    </tr>
"""
        
        html += """
                </tbody>
            </table>
        </div>
    </div>
</body>
</html>
"""
        
        return html
        
    def has_regressions(self, baseline_dir: str, regression_threshold: float = 1.2) -> Tuple[bool, List[Dict]]:
        """Check if current run has regressions compared to baseline.
        
        Returns:
            Tuple of (has_regressions: bool, regressions: List[Dict])
        """
        baseline_analyzer = E2ETestAnalyzer(baseline_dir)
        if not baseline_analyzer.load_test_data():
            return False, []
            
        current_perf = self.analyze_performance()
        baseline_perf = baseline_analyzer.analyze_performance()
        
        comparison = self._compare_runs(current_perf, baseline_perf, regression_threshold)
        
        return len(comparison['regressions']) > 0, comparison['regressions']


def main():
    parser = argparse.ArgumentParser(description="Analyze ACM Switchover E2E test results")
    parser.add_argument("--results-dir", required=True, help="Results directory path")
    parser.add_argument("--output", default="./e2e_analysis_report.html", help="Output HTML report file")
    parser.add_argument("--compare", metavar="BASELINE_DIR", 
                        help="Compare against a baseline results directory")
    parser.add_argument("--regression-threshold", type=float, default=1.2,
                        help="Regression threshold multiplier (default: 1.2 = 20%% slower)")
    parser.add_argument("--check-regressions", action="store_true",
                        help="Exit with code 1 if regressions are detected (for CI)")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    
    args = parser.parse_args()
    
    # Handle glob patterns
    if '*' in args.results_dir:
        from glob import glob
        matching_dirs = glob(args.results_dir)
        if len(matching_dirs) == 0:
            print(f"No directories found matching: {args.results_dir}")
            sys.exit(1)
        elif len(matching_dirs) > 1:
            print(f"Multiple directories found matching: {args.results_dir}")
            for dir_path in matching_dirs:
                print(f"  - {dir_path}")
            print("Please specify a single directory for analysis")
            sys.exit(1)
        else:
            args.results_dir = matching_dirs[0]
    
    # Create analyzer and load data
    analyzer = E2ETestAnalyzer(args.results_dir)
    
    if not analyzer.load_test_data():
        print("Failed to load test data")
        sys.exit(1)
    
    # Comparison mode
    if args.compare:
        # Generate comparison report
        comparison_output = args.output.replace('.html', '_comparison.html')
        if not analyzer.generate_comparison_report(args.compare, comparison_output, args.regression_threshold):
            print("Failed to generate comparison report")
            sys.exit(1)
            
        # Check for regressions if requested
        if args.check_regressions:
            has_regressions, regressions = analyzer.has_regressions(args.compare, args.regression_threshold)
            if has_regressions:
                print(f"\n⚠️  {len(regressions)} regression(s) detected:")
                for reg in regressions:
                    print(f"  - {reg['message']}")
                sys.exit(1)
            else:
                print("\n✓ No regressions detected")
    else:
        # Standard report
        if analyzer.generate_html_report(args.output):
            print(f"Analysis complete! Report saved to: {args.output}")
        else:
            print("Failed to generate report")
            sys.exit(1)


if __name__ == "__main__":
    main()
