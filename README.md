## IBMCLOUD-CREATE-VPC

This Python code reads a yaml based topology configuration file and instantiates the specified VPC network based onthe YAML configuration on the IBM Cloud using the RIAS APII.

## Configuring your VPC topology
The topology is configured using standard YAML format

At the top of the yaml file define the VPC name, region location, and characteristics.
```
- vpc: "vpc name"
    region: "region_name"
    classic_access: True or False
    resource_group: "resource_group name"
    default_network_acl:
      name: "network_acl_name"
```
After you have defined the VPC, you should define the subnets required within each zone of the multi-zone region.
```
zones:
      - name: "zone_name"
        subnets:
        - subnet: "subnet-name"
          ipv4_cidr_block: "IPV4 CIDR block notation"
          network_acl: "network_acl to assign to subnet"
          publicGateway: True or False
      - name: "zone_name"
        subnets:
        - subnet: "subnet-name"
          ipv4_cidr_block: "IPV4 CIDR block notation"
          network_acl: "network_acl to assign to subnet"
          publicGateway: True or False
      - name: "us-south-3"
        subnets:
        - subnet: "subnet-name"
          ipv4_cidr_block: "IPV4 CIDR block notation"
          network_acl: "network_acl to assign to subnet"
          publicGateway: True or False
```
Additional Subnets blcoks can be specified in each zone.    However, only one subnet per zone can have a public gateway specified.

To identify the available regions, you need the Infrastructure Services plugin for the IBMCLOUD CLi.   More information can be found about installing
the CLI and plugins at: [https://console.bluemix.net/docs/cli/index.html#overview](https://console.bluemix.net/docs/cli/index.html#overview)
```
ibmcloud login --sso
ibmcloud is regions
```
To identify the available zones within a region.
```
ibmcloud login --sso
ibmcloud is regions region_name
```
To execute the code you must first authenticate with the IBM Cloud by using Identity and Access Management (IAM).  This script requires that you first request a bearer token and store
it in a file called iam_token.   This token is used in an Authorization header for the REST API calls, and is only valid for one hour.  To 
request the token follow the following steps

```
ibmcloud login --sso
iam_token=$(ibmcloud iam oauth-tokens | awk '/IAM/{ print $3 " " $4; }')
echo $iam_token > iam_token
```
Alternatively you can run the following command to generate the file:
```
./gettoken.sh
```

Once complete execute the Python code and build the specified VPC
```
./provision-vpc.py
```
