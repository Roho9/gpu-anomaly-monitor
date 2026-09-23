import * as cdk from "aws-cdk-lib";
import * as fs from "fs";
import * as path from "path";
import { Construct } from "constructs";
import * as s3 from "aws-cdk-lib/aws-s3";
import * as s3deploy from "aws-cdk-lib/aws-s3-deployment";
import * as cloudfront from "aws-cdk-lib/aws-cloudfront";
import * as origins from "aws-cdk-lib/aws-cloudfront-origins";

export interface FrontendStackProps extends cdk.StackProps {
  albDnsName: string;
}

/**
 * React frontend delivery: S3 (private, origin-access-control) + CloudFront.
 *
 * CloudFront serves the built React app from S3 and routes the API/WebSocket
 * paths to the ALB origin, so the app can use same-origin relative URLs (no
 * CORS, and WebSockets to /live work through the CDN). This is the production
 * counterpart to the bundled dashboard.html + the Vite dev proxy.
 *
 * Run `npm --prefix ../frontend run build` before `cdk deploy` to publish the
 * latest assets; synth still succeeds without a build (deployment is skipped).
 */
export class FrontendStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props: FrontendStackProps) {
    super(scope, id, props);

    const bucket = new s3.Bucket(this, "SiteBucket", {
      encryption: s3.BucketEncryption.S3_MANAGED,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
      autoDeleteObjects: true,
    });

    const albOrigin = new origins.HttpOrigin(props.albDnsName, {
      protocolPolicy: cloudfront.OriginProtocolPolicy.HTTP_ONLY,
    });
    // API/WS paths bypass caching and forward everything to the backend.
    const apiBehavior: cloudfront.BehaviorOptions = {
      origin: albOrigin,
      viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
      allowedMethods: cloudfront.AllowedMethods.ALLOW_ALL,
      cachePolicy: cloudfront.CachePolicy.CACHING_DISABLED,
      originRequestPolicy: cloudfront.OriginRequestPolicy.ALL_VIEWER,
    };

    const distribution = new cloudfront.Distribution(this, "Cdn", {
      defaultRootObject: "index.html",
      defaultBehavior: {
        origin: origins.S3BucketOrigin.withOriginAccessControl(bucket),
        viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
      },
      additionalBehaviors: {
        "/health": apiBehavior,
        "/events": apiBehavior,
        "/live": apiBehavior,
        "/incidents": apiBehavior,
        "/incidents/*": apiBehavior,
        "/history/*": apiBehavior,
      },
      // SPA fallback so client routes resolve to index.html.
      errorResponses: [
        { httpStatus: 403, responseHttpStatus: 200, responsePagePath: "/index.html" },
        { httpStatus: 404, responseHttpStatus: 200, responsePagePath: "/index.html" },
      ],
    });

    // Publish the built assets if they exist (skip during a bare CI synth).
    const dist = path.join(__dirname, "..", "..", "frontend", "dist");
    if (fs.existsSync(dist)) {
      new s3deploy.BucketDeployment(this, "DeploySite", {
        sources: [s3deploy.Source.asset(dist)],
        destinationBucket: bucket,
        distribution,
        distributionPaths: ["/*"],
      });
    }

    new cdk.CfnOutput(this, "FrontendUrl", { value: `https://${distribution.distributionDomainName}` });
  }
}
