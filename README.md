## IBMCLOUD-CREATE-VPC

This Python based program reads a yaml based topology configuration file and instantiates the specified VPC network and compute 
resources based on the YAML configuration specified using the RIAS REST API.  This approach allows for templating and 
consistency between application tiers, subnets, zones, and regions avoiding the need to manually define resources via a portal
or CLI and avoiding having to create your own provisioning scripts.

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
To configure to types of virtual servers or instances to be provisioned in each of the subnets you first define an instance
template for each of the instances types you plan to provisioned.

```
instanceTemplates:
      - template: "web_server"
        image_id:  "7eb4e35b-4257-56f8-d7da-326d85452591"
        profile_name: "c-2x4"
        sshkey_id: "636f6d70-0000-0001-0000-00000014ba47" 
        cloud-init-file: "cloud-init.txt"
      - template: "db_server"
        image_id:  "7eb4e35b-4257-56f8-d7da-326d85452591" 
        profile_name: "c-2x4"
        sshkey_id: "636f6d70-0000-0001-0000-00000014ba47" 
        cloud-init-file: "cloud-init.txt"
```
The instanceTemplates will be used during provisioning and sets the values used for the instances requested.

Post provisioning cloud-init scripts are used to do post provisioning installation tasks, and can be passed as
user data to the operating system by including a filename in the "cloud-init-file" parameter.   The cloud-init
file must begin with a #cloud-config and will be properly encoded and passed via the user_data parameter.

Profile_name must be a valid profile_name.   Profiles represent the memory and cpu resources of the virtual machine. 
To identify available profiles in each region:

```
ibmcloud is instance profiles
```

Images determine the cloud-init image to use for provisioning.  Include the image_id.  To identify available images

```
ibmcloud is images
```

To list the ssh_key's in your account
```
ibmcloud is keys
```

To provision instances within each subnet include the following instances section in each subnet.

```
          instances:
              - instance: "web%02d-us-south-1"
                quantity: 2
                template: "web_server"
                floating_ip: true
```

Instance name is derived from the text provided for "instance".   Use %02d to represent numeric number which will
be generated sequentially during provisioning.    The number of instances provisioned in the specified subnet is determined
by the Quantity parameter, and the template specified must match a instanceTemplate defined elsewhere in the yaml file.

### Execute Script
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
