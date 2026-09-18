import * as cdk from "aws-cdk-lib";
import { Construct } from "constructs";
import * as ec2 from "aws-cdk-lib/aws-ec2";

/**
 * VPC for the containerised processing service (ECS/Fargate) and any
 * VPC-bound Lambdas. Two AZs, private egress via NAT, plus gateway/interface
 * endpoints so traffic to S3, DynamoDB, Kinesis and Bedrock stays on the AWS
 * network instead of traversing the public internet.
 */
export class NetworkingStack extends cdk.Stack {
  public readonly vpc: ec2.Vpc;

  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);

    this.vpc = new ec2.Vpc(this, "Vpc", {
      maxAzs: 2,
      natGateways: 1,
      subnetConfiguration: [
        { name: "public", subnetType: ec2.SubnetType.PUBLIC, cidrMask: 24 },
        { name: "private", subnetType: ec2.SubnetType.PRIVATE_WITH_EGRESS, cidrMask: 24 },
      ],
    });

    this.vpc.addGatewayEndpoint("S3Endpoint", { service: ec2.GatewayVpcEndpointAwsService.S3 });
    this.vpc.addGatewayEndpoint("DynamoEndpoint", { service: ec2.GatewayVpcEndpointAwsService.DYNAMODB });
    this.vpc.addInterfaceEndpoint("KinesisEndpoint", { service: ec2.InterfaceVpcEndpointAwsService.KINESIS_STREAMS });
    this.vpc.addInterfaceEndpoint("BedrockEndpoint", { service: ec2.InterfaceVpcEndpointAwsService.BEDROCK_RUNTIME });

    new cdk.CfnOutput(this, "VpcId", { value: this.vpc.vpcId });
  }
}
