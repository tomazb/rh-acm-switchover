#!/bin/bash
# Quick start helper script for ACM switchover

set -e

echo "================================================"
echo "ACM Hub Switchover - Quick Start"
echo "================================================"
echo ""

# Check if Python is installed
if ! command -v python3 &> /dev/null; then
    echo "Error: Python 3 is required but not found"
    echo "Please install Python 3.9 or later"
    exit 1
fi

echo "✓ Python 3 found: $(python3 --version)"

# Check if kubectl/oc is installed
if command -v oc &> /dev/null; then
    echo "✓ OpenShift CLI found: $(oc version --client | head -n1)"
elif command -v kubectl &> /dev/null; then
    echo "✓ Kubernetes CLI found: $(kubectl version --client --short 2>/dev/null || kubectl version --client)"
else
    echo "Error: kubectl or oc CLI is required but not found"
    echo "Please install OpenShift CLI or kubectl"
    exit 1
fi

# Install Python dependencies
echo ""
echo "Installing Python dependencies..."
if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv
fi

echo "Activating virtual environment..."
source venv/bin/activate

echo "Installing requirements..."
pip install -q --upgrade pip
pip install -q -r requirements.txt

echo "✓ Dependencies installed"
echo ""

# Interactive setup
echo "================================================"
echo "Configuration"
echo "================================================"
echo ""
echo "Please provide your Kubernetes/OpenShift contexts:"
echo ""

# Get available contexts
echo "Available contexts:"
if command -v oc &> /dev/null; then
    oc config get-contexts -o name | sed 's/^/  - /'
else
    kubectl config get-contexts -o name | sed 's/^/  - /'
fi

echo ""
read -p "Primary hub context: " PRIMARY_CONTEXT
read -p "Secondary hub context: " SECONDARY_CONTEXT
echo ""

# Choose method
echo "Switchover method:"
echo "  1. Passive sync (recommended - for continuous backup sync)"
echo "  2. Full restore (for new secondary hub or one-time restore)"
echo ""
read -p "Choose method (1 or 2) [1]: " METHOD_CHOICE
METHOD_CHOICE=${METHOD_CHOICE:-1}

if [ "$METHOD_CHOICE" = "1" ]; then
    METHOD="passive"
else
    METHOD="full"
fi

echo ""
echo "================================================"
echo "Ready to Start"
echo "================================================"
echo ""
echo "Configuration:"
echo "  Primary hub:    $PRIMARY_CONTEXT"
echo "  Secondary hub:  $SECONDARY_CONTEXT"
echo "  Method:         $METHOD"
echo ""

# Offer to run validation
read -p "Run validation checks? (recommended) [Y/n]: " RUN_VALIDATION
RUN_VALIDATION=${RUN_VALIDATION:-Y}

if [[ "$RUN_VALIDATION" =~ ^[Yy]$ ]]; then
    echo ""
    echo "Running validation checks..."
    echo ""
    
    python acm_switchover.py \
        --validate-only \
        --primary-context "$PRIMARY_CONTEXT" \
        --secondary-context "$SECONDARY_CONTEXT" \
        --method "$METHOD" \
        --verbose
    
    VALIDATION_RESULT=$?
    
    if [ $VALIDATION_RESULT -eq 0 ]; then
        echo ""
        echo "✓ Validation passed!"
        echo ""
        
        # Offer to run dry-run
        read -p "Run dry-run to preview actions? (recommended) [Y/n]: " RUN_DRYRUN
        RUN_DRYRUN=${RUN_DRYRUN:-Y}
        
        if [[ "$RUN_DRYRUN" =~ ^[Yy]$ ]]; then
            echo ""
            echo "Running dry-run..."
            echo ""
            
            python acm_switchover.py \
                --dry-run \
                --primary-context "$PRIMARY_CONTEXT" \
                --secondary-context "$SECONDARY_CONTEXT" \
                --method "$METHOD" \
                --verbose
            
            echo ""
            echo "Dry-run complete."
            echo ""
        fi
        
        # Offer to execute
        echo "================================================"
        echo "Execute Switchover"
        echo "================================================"
        echo ""
        echo "Ready to execute switchover."
        echo "This will:"
        echo "  1. Pause backups on primary hub"
        echo "  2. Disable auto-import on managed clusters"
        echo "  3. Activate secondary hub"
        echo "  4. Wait for clusters to connect to secondary"
        echo "  5. Enable backups on secondary hub"
        echo ""
        echo "Estimated time: 30-60 minutes"
        echo ""
        
        read -p "Execute switchover now? [y/N]: " EXECUTE
        
        if [[ "$EXECUTE" =~ ^[Yy]$ ]]; then
            echo ""
            echo "Starting switchover..."
            echo ""
            
            python acm_switchover.py \
                --primary-context "$PRIMARY_CONTEXT" \
                --secondary-context "$SECONDARY_CONTEXT" \
                --method "$METHOD" \
                --verbose
            
            SWITCHOVER_RESULT=$?
            
            if [ $SWITCHOVER_RESULT -eq 0 ]; then
                echo ""
                echo "================================================"
                echo "✓ Switchover completed successfully!"
                echo "================================================"
                echo ""
                echo "Next steps:"
                echo "  1. Verify managed clusters on secondary hub:"
                echo "     oc --context $SECONDARY_CONTEXT get managedclusters"
                echo ""
                echo "  2. Check backup schedule on secondary hub:"
                echo "     oc --context $SECONDARY_CONTEXT get backupschedule -n open-cluster-management-backup"
                echo ""
                echo "  3. To decommission old primary hub:"
                echo "     python acm_switchover.py --decommission --primary-context $PRIMARY_CONTEXT"
                echo ""
            else
                echo ""
                echo "================================================"
                echo "✗ Switchover failed or was interrupted"
                echo "================================================"
                echo ""
                echo "To resume from last successful step, re-run:"
                echo "  python acm_switchover.py \\"
                echo "    --primary-context $PRIMARY_CONTEXT \\"
                echo "    --secondary-context $SECONDARY_CONTEXT \\"
                echo "    --method $METHOD \\"
                echo "    --verbose"
                echo ""
                echo "To rollback to primary hub:"
                echo "  python acm_switchover.py --rollback \\"
                echo "    --primary-context $PRIMARY_CONTEXT \\"
                echo "    --secondary-context $SECONDARY_CONTEXT"
                echo ""
            fi
        else
            echo ""
            echo "Switchover cancelled."
            echo ""
            echo "To execute later, run:"
            echo "  source venv/bin/activate"
            echo "  python acm_switchover.py \\"
            echo "    --primary-context $PRIMARY_CONTEXT \\"
            echo "    --secondary-context $SECONDARY_CONTEXT \\"
            echo "    --method $METHOD \\"
            echo "    --verbose"
            echo ""
        fi
    else
        echo ""
        echo "✗ Validation failed!"
        echo ""
        echo "Please fix the validation errors before proceeding."
        echo "Review the output above for specific issues."
        echo ""
        exit 1
    fi
else
    echo ""
    echo "Validation skipped."
    echo ""
    echo "To run manually:"
    echo "  source venv/bin/activate"
    echo "  python acm_switchover.py --validate-only \\"
    echo "    --primary-context $PRIMARY_CONTEXT \\"
    echo "    --secondary-context $SECONDARY_CONTEXT \\"
    echo "    --method $METHOD"
    echo ""
fi
