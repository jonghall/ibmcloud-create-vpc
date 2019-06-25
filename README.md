## IBMCLOUD-CREATE-VPC
A typical requirement for a Virtual Private Cloud is the ability to logically isolate a public cloud into different private networks made up tiers and/or different applications environments.   This Python script reads a yaml based topology configuration file and instantiates the specified VPC, security-groups, network acls, subnets, compute resources and the required public and private load balancers accross the desired availability zones within the region specified to standup the application topology withouht manually creating it.

This approach allows for templating and consistency between application tiers, subnets, zones, and regions avoiding the need to manually define resources via a portal or CLI and avoiding the need to create your own provisioning scripts.

## A Typical Enterprise Application Topology
A typical E-commerce web app might be deployed accross 3 zones consisting of 3 segmented network tiers using IBM Cloud Object Storage for images/media, IBM Cloud Databases such as PostgrSQL or MySQL or self managed databases running on virtual servers and Redis for database session cache, and a VPN for on-premise API services and management.  Separate VPCs are created to completely isolate PROD from DEV environments.  

![](topology.png?raw=true)

## Configuring your VPC topology
The topology is configured using standard YAML format

The first step is to define within the yaml file the VPC name, region location, and characteristics of the vpc.
```
-
  vpc: vpcname
  region: us-south
  classic_access: false
  resource_group: default
  default_network_acl: vpc-acl
```
Referenced by the VPC is the default network ACL to use.   You can specify one that exists already, or define one within the YAML file.  The default network ACL is used as the default for all subnets created later and controls both ingress and egress traffic out of the subnets.   It is recommended that the default ACL deny traffic or restrict traffic to prevent an exposure.  The per subnet ACL's can be created and assigned which allow traffic based on the requirements of that application tier.
```
-
    network_acls:
      - network_acl: ecommerce-vpc-default-acl
        rules:
          - name: deny-all-in
            direction: inbound
            action: deny
            source: 0.0.0.0/0
            destination: 0.0.0.0/0
          - name: deny-all-out
            direction: outbound
            action: deny
            source: 0.0.0.0/0
            destination: 0.0.0.0/0
```
Next, by default VPC's are created with a 10.240.0.0/18, 10.240.64.0/18, and 10.240.128.0/18 for non classic access VPCs.   Classic Access VPC's instead use 172.16.0.0/18, 172.16.64.0/18, and 172.16.128.0/18 for the address space of each zone.   However, if you prefer to use a different address prefix for the subnets in each zone, it can be specified as part of the Zone configuration.   In the example below a netmask of /18 is used to define the IP Address space in the zone 1 of the US South region.   

```
zones:
    -
      name: us-south-1
      address_prefix_cidr: 172.16.0.0/18 
```

After you have defined the VPC, and defined the address space for the zone, you must define the subnets required within each zone of the multi-zone region.  Subnet's are defined with CIDR block notation, and must be allocated out of the CIDR block defined for the availability zone.   Subnets can not overlap eachother.   Multiple subnets can be defined per zone.  If egress traffic will be allowed to the public Internet specify  PublicGateway: true parameter.   A network_acl can be specified and will be assigned instead of the default.   If it does not already exist, one can be created in the network_acls section of the YAML file.
```
 zones:
    -
      name: us-south-1
      address_prefix_cidr: 172.20.0.0/18
      subnets:
        -
          name: subnet-name
          ipv4_cidr_block: 172.16.0.0/24
          network_acl: network-acl
          publicGateway: true
```

To access your VPC you will need to define a VPNaaS instance.    A VPN instance is required for each zone of the VPC, and multiple connections can be defined to the VPN instance for connectivity to your premise or other VPCs.  Specify the remote public address of the VPN device in peer_address.  If it is behind a NAT device use the address 0.0.0.0.   Specify the preshared_key and peer_cidrs to be connected through the connection.   Multiple connections can be created.

```
          vpn:
            - name: webtier-us-south-1-vpn
              connections:
                - name: on-prem-to-vpc-us-south-1
                  peer_address: 0.0.0.0
                  preshared_key: mypresharedkey
                  peer_cidrs:
                    - 10.0.0.0/8
```

To identify the available regions, you need the Infrastructure Services plugin for the IBMCLOUD CLi.   More information can be found about installing the CLI and plugins at: [https://console.bluemix.net/docs/cli/index.html#overview](https://console.bluemix.net/docs/cli/index.html#overview)
```
ibmcloud login --sso
ibmcloud is regions
```
To identify the available zones within a region.
```
ibmcloud login --sso
ibmcloud is regions region_name
```
Within each subnet instances can be provisioned.  To faciliate consistent provisioning of virtual instances a template must first be defined for each server type you wish to provision.   Within each template you will define the base OS image id to use, the ssh key ID to use, and the type of virtual server profile to use.   Last within each template you can specify a cloud-init post installation script to be deployed.  The cloud-init file must begin with a #cloud-config and will be properly encoded and passed via the user_data parameter during the provisioning process and executed during the provisioning process.

```
  instanceTemplates:
    -
      template: web_server
      image: ubuntu-16.04-amd64
      profile_name: c-2x4
      sshkey: my-ssh-key 
      cloud-init-file: cloud-init.txt
```
The instanceTemplates will be used during provisioning and sets the values used for each of the instances requested.

Profile_name must be a valid profile_name.   Profiles represent the memory and cpu resources of the virtual machine. 
To identify available profiles in each region:

```
ibmcloud is instance-profiles
```
Image must be a valid image name.  To determine the images available to use for provisioning.  
```
ibmcloud is images
```
The sshkey must be a valid sshkey defined in the account.  To list the ssh_key's in your region
```
ibmcloud is keys
```
# SSH Keys
You may define multiple SSH keys to be created by adding the following section to the YAML file.  You can then reference these keys in the instanceTemplates section.
```
   sshkeys:
      -
        sshkey: my-ssh-key
        public_key: ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQDQ6H4W/5PCtVb6BEgbxNdgDbrJsAFD/Y13mz+qVhM6kHmoOBu5tbbQh7LGfCjpHzZ2A59m2i3zpFNwA9r06UErIfG8U020QAnirrmpo1qqB9tMI7BRSyvf5NFXnUklyszQSsXxxM6eYiQLiHDNnVN7Qyzgq5YcZ8eb559KzmyretdPulEBQvWKZyUbE03kX8ScNTI87p/jX/464viudryjtLgUNuJoFtCCYdoolvnNZsAq3wBl9LOgNaT33nP1ys1R4azG3pC921WX5+g4txws7tVzjPB/e5caOYdGXbFnYi2TXY3agX0wCNj/p/nPEO29c7s7kzEZN9o8ygSrj+Yn
```
# Security Groups
Security Groups define the egress and ingress traffic from each virtual server instance.   Security Groups can be defined in the security_groups section of the YAML file and are referenced during creation of instances.   Multiple inbound and outbound rules can be defined.   Security group rules can reference CIDR Blocks, Single IP addresses, or other Security Groups.   Security groups are generally fine grained and control traffic flow between and accross tiers of an application topology.

```
  security_groups:
    -
      security_group: ecommerce-vpc-apptier2
      rules:
        -
          direction: inbound
          ip_version: ipv4
          protocol: all
          remote:
            cidr_block: 172.16.16.0/24
         -
          direction: inbound
          ip_version: ipv4
          protocol: all
          remote:
            cidr_block: 172.16.80.0/24
        -
          direction: inbound
          ip_version: ipv4
          protocol: all
          remote:
            cidr_block: 172.16.144.0/24
        -
          direction: inbound
          ip_version: ipv4
          port_min: 8090
          port_max: 8091
          protocol: tcp
          remote:
            security_group: ecommerce-vpc-webtier
        -
          direction: outbound
          ip_version: ipv4
          protocol: all
          remote:
            cidr_block: 0.0.0.0/0
```
Within the zone and subnet section of the YAML file you define the actual virtual server instances to be provisioned in that subnet.   You specify a name template, the quantity required, the server template previously defined which will be used to provision the server, and the security group to attach to the network interface.  
```
    instances:
      -
        name: web%02d-zone1.mydomain.com
        quantity: 4
        template: web_server
        floating_ip: true
        security_group: jonhall-vpctest01-webtier
        in_lb_pool:
          -
            lb_name: ecommerce-vpc-webtier-lb01
            lb_pool: nginx-http
            listen_port: 80
```
The instance name of each virtual server provisioned will be derived from the text provided in the "name" parameter.   Use %02d to represent a sequetial numeric number which will be generated sequentially during provisioning.

If the instances will need to be added to a load balancer pool use the "in_lib_pool" and specify the lb_name and lb_pool for the desired load balancer the instances will be added behind.  Additionally specify the port for which the application will run on using the listen_port paramter.  You may specify multiple Load Balancers and pools per instance.

Multiple instance types can be configured in each subnet.

If the load balancers do not already exist within the VPC, the load balancer configuration should be specified in the load_balancers section of the YAML file and it will be created.

Specify the Load Balancer characteristics, location of load balancer nodes, listeners, pools and health-monitors can be defined for multiple public and private load balancers.    Pool members are determined by the specifications within the instance section of each subnet.

```
load_balancers:
      lbInstance: ecommerce-vpc-apptier-lb01
      is_public: false
      subnets:
        - app-tier-us-south-1
        - app-tier-us-south-2
      listeners:
        -
          protocol: http
          port: 8090
          connection_limit: 100
          default_pool_name: index_php_upstream
      pools:
        - name: index_php_upstream
          protocol: http
          algorithm: least_connections
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
Once complete execute the Python code to build the specified VPC and required application topology.   If elements of the VPC already exist, the script will identify the state and move to the next element.   By default the script reads the topology.yaml file, but you can specify a different topology file by using --yaml filename.

```
./provision-vpc.py [--yaml filename]
```

To destroy the VPC created, and systematically delete all objects in the YAML file run: 
```
./destroy-vpc.py [--yaml filename]
```

## Known Limitations  
- Only one VPC can be defined in YAML file
- Only parameters shown in YAML file are currently supported
- If objects already exist, script will not recreate the object and therefore does not evaluate if changes exist.   You must manually delete prior to execution of script if you want the changes to be implemented.
