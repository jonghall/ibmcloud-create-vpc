## IBMCLOUD-CREATE-VPC
A typical requirement for a Virtual Private Cloud is the ability to logically isolate a public cloud into different private networks made up tiers and different applications environments.   This Python program reads a yaml based topology configuration file and instantiates the specified VPC, subnets, compute resources and the required public and private load balancers accross the desired availability zones within a region to standup the application topology.

This approach allows for templating and consistency between application tiers, subnets, zones, and regions avoiding the need to manually define resources via a portal or CLI and avoiding the need to create your own provisioning scripts.

## Typical Application Topology
A typical Ecommerce web app deployed accross 3 zones consisting of 3 segmented network tiers using IBM Cloud Object Storage for images/media, IBM Cloud Databases for Redis for application cache, and VPN for on-premise API services.  Separate VPCs are created to completely isolate PROD from DEV.  DEV also consists of a utility server for DevOps with VPN connectivity between VPCs.

![](topology.png?raw=true)

## Configuring your VPC topology
The topology is configured using standard YAML format

Within the yaml file define the VPC name, region location, and characteristics.
```
-
  vpc: vpcname
  region: us-south
  classic_access: false
  resource_group: default
  default_network_acl: vpc-acl
```
Next for each VPC you will define your IP CDIR block for each of the availability zones you will plan.   In the example below a netmask of /18 is used to define the IP Address space for each of the availability zones in the US South region. 

```
  address_prefix:
    -
      name: address-prefix-zone-1-nane
      zone: us-south-1
      cidr: 172.16.0.0/18
    -
      name: address-prefix-zone-2-name
      zone: us-south-2
      cidr: 172.16.64.0/18
    -
      name: address-prefix-zone-3-name
      zone: us-south-3
      cidr: 172.16.128.0/18
```
After you have defined the VPC, you must define the subnets required within each availability zone of the multi-zone region.  Subnet's are defined with CIDR block notation, and must be allocated out of the CIDR block defined previously for each availability zone.   Subnets can not overlap eachother.   Multiple subnets can be defined per zone.  However, only one subnet per zone can be configured for egress traffic via the PublicGateway: True parameter.
```
 zones:
    -
      name: us-south-1
      subnets:
        -
          name: subnet-name
          ipv4_cidr_block: 172.16.0.0/24
          network_acl: network-acl
          publicGateway: true
```

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
Within each subnet instances can be provisioned.  To faciliate consistent provisioning of virtual services a template must first be defined for each server type.   Within each template you will define  the base OS image id to use, the ssh key ID to use, and the type of virtual server profile to use.   Last within each template you can specify a cloud-init post installation script to be deployed.  The cloud-init
file must begin with a #cloud-config and will be properly encoded and passed via the user_data parameter during the provisioning process.

```
  instanceTemplates:
    -
      template: web_server
      image_id: 7eb4e35b-4257-56f8-d7da-326d85452591
      profile_name: c-2x4
      sshkey_id: 636f6d70-0000-0001-0000-00000014ba47
      cloud-init-file: cloud-init.txt
```
The instanceTemplates will be used during provisioning and sets the values used for each of the instances requested.

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
After instanceTemplates have been defined within each subnet resource you can specify the quantity of each server type, the naming template, and whether the server should be added to a load balancer pool after provisioning.  

To provision instances, include the instances section in the desired subnet sections of the YAML file by including the following text.
```
instances:
            -
              name: web%02d-zone1.mydomain.com
              quantity: 4
              template: web_server
              floating_ip: true
              security_group: jonhall-vpctest01-webtier
              lb_name: my-lb01
              lb_pool: http-app1
              listen_port: 80
```
Instance name will be derived from the text provided ub the "name" parameter.   Use %02d to represent numeric number which will
be generated sequentially during provisioning.    The number of instances provisioned in the specified subnet is determined
by the Quantity parameter, and the template specified must match a instanceTemplate defined elsewhere in the yaml file.

If the instances will be added to a load balancer pool specify the lb_name and lb_pool for the desired load balancer.  Additionally specify the port for which the application will run on using the listen_port paramter.

Multiple instance types can be configured in each subbet.

If the load balancers do not already exist within the VPC, the load balancer configuration can be specified in the load_balancers section of the YAML file under each VPC.

The Load Balancer characteristics, location of load balancer nodes, listeners, pools and health-monitors can be defined for multiple public and private load balancers.    Pool members are determined by the specifications within the instance section of each subnet.

```
load_balancers:
    -
      lbInstance: jonhall-vpctest01-webtier-lb01
      is_public: true
      subnets:
        - web-tier-us-south-1
        - web-tier-us-south-2
      listeners:
        -
          protocol: http
          port: 80
          connection_limit: 100
          default_pool_name: http-app1
      pools:
        -
          name: http-app1
          protocol: http
          algorithm: round_robin
          health_monitor:
            type: http
            delay: 5
            max_retries: 2
            timeout: 2
            url_path: /
```

### Execute Script
Once the YAML configuraiton file is complete you must first authenticate with the IBM Cloud by using Identity and Access Management (IAM).  This script requires that you first request a bearer token and store it in a file called iam_token.   This token is used in an Authorization header for the REST API calls, and is only valid for one hour.  To request the token follow the following steps
```
ibmcloud login --sso
iam_token=$(ibmcloud iam oauth-tokens | awk '/IAM/{ print $3 " " $4; }')
echo $iam_token > iam_token
```
Alternatively you can run the provide script and the following command will request the token and generate the file:
```
./gettoken
```
Once complete execute the Python code to build the specified VPC and required application topology.   If elements of the VPC already exist, the script will identify the state and move to the next element.

```
./provision-vpc.py
```
