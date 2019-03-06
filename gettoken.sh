#!/usr/bin/env bash
ibmcloud login --sso
iam_token=$(ibmcloud iam oauth-tokens | awk '/IAM/{ print $3 " " $4; }')
echo $iam_token>iam_token

