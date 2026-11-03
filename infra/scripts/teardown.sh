#!/usr/bin/env bash
# Delete everything created for the capstone so it stops costing money.
# The Ingress is deleted first so the Load Balancer Controller removes the ALB;
# otherwise the ALB would block VPC deletion.
set -euo pipefail

CLUSTER=finsecure-eks
REGION=ap-southeast-1

read -r -p "This permanently deletes cluster ${CLUSTER}. Type the cluster name to confirm: " answer
[[ "$answer" == "$CLUSTER" ]] || { echo "aborted"; exit 1; }

aws eks update-kubeconfig --name "$CLUSTER" --region "$REGION"
kubectl -n finsecure delete ingress --all --ignore-not-found --wait=true
kubectl -n monitoring delete ingress --all --ignore-not-found --wait=true
echo "Waiting 60s for the ALBs to be deprovisioned..."
sleep 60
eksctl delete cluster --name "$CLUSTER" --region "$REGION" --wait
echo "Done. Check the EC2 > Load Balancers console for anything left behind."
