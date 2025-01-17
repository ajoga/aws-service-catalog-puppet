import json

import os
import re
import time

import luigi
from betterboto import client as betterboto_client

from servicecatalog_puppet import aws
from servicecatalog_puppet import config

from servicecatalog_puppet.workflow import provisioning
from servicecatalog_puppet.workflow import tasks

import logging

logger = logging.getLogger("tasks")


class GetVersionIdByVersionName(tasks.PuppetTask):
    portfolio = luigi.Parameter()
    product = luigi.Parameter()
    version = luigi.Parameter()
    account_id = luigi.Parameter()
    region = luigi.Parameter()

    def params_for_results_display(self):
        return {
            "account_id": self.account_id,
            "region": self.region,
            "portfolio": self.portfolio,
            "product": self.product,
            "version": self.version,
        }

    def output(self):
        return luigi.LocalTarget(
            f"output/GetVersionIdByVersionName/"
            f"{self.account_id}-{self.region}-{self.portfolio}-{self.product}-{self.version}.json"
        )

    def requires(self):
        product_id = GetProductIdByProductName(
            self.portfolio,
            self.product,
            self.account_id,
            self.region,
        )
        return {
            'product': product_id,
        }

    def run(self):
        with self.input().get('product').open('r') as f:
            product_details = json.loads(f.read())
        with betterboto_client.CrossAccountClientContextManager(
                'servicecatalog',
                f"arn:aws:iam::{self.account_id}:role/servicecatalog-puppet/PuppetRole",
                f"{self.account_id}-{self.region}",
                region_name=self.region
        ) as cross_account_servicecatalog:
            version_id = aws.get_version_id_for(
                cross_account_servicecatalog,
                product_details.get('product_id'),
                self.version
            )
            with self.output().open('w') as f:
                f.write(
                    json.dumps(
                        {
                            'version_name': self.version,
                            'version_id': version_id,
                            'product_name': product_details.get('product_name'),
                            'product_id': product_details.get('product_id'),
                        },
                        indent=4,
                        default=str,
                    )
                )


class GetProductIdByProductName(tasks.PuppetTask):
    portfolio = luigi.Parameter()
    product = luigi.Parameter()
    account_id = luigi.Parameter()
    region = luigi.Parameter()

    def params_for_results_display(self):
        return {
            "account_id": self.account_id,
            "region": self.region,
            "portfolio": self.portfolio,
            "product": self.product,
        }

    def requires(self):
        portfolio_id = GetPortfolioIdByPortfolioName(
            self.portfolio,
            self.account_id,
            self.region,
        )
        return {
            'portfolio': portfolio_id,
        }

    def output(self):
        return luigi.LocalTarget(
            f"output/GetProductIdByProductName/"
            f"{self.account_id}-{self.region}-{self.portfolio}-{self.product}.json"
        )

    def run(self):
        with self.input().get('portfolio').open('r') as f:
            portfolio_details = json.loads(f.read())
        with betterboto_client.CrossAccountClientContextManager(
                'servicecatalog',
                f"arn:aws:iam::{self.account_id}:role/servicecatalog-puppet/PuppetRole",
                f"{self.account_id}-{self.region}",
                region_name=self.region
        ) as cross_account_servicecatalog:
            product_id = aws.get_product_id_for(
                cross_account_servicecatalog,
                portfolio_details.get('portfolio_id'),
                self.product,
            )
            with self.output().open('w') as f:
                f.write(
                    json.dumps(
                        {
                            'product_name': self.product,
                            'product_id': product_id,
                            'portfolio_name': portfolio_details.get('portfolio_name'),
                            'portfolio_id': portfolio_details.get('portfolio_id'),
                        },
                        indent=4,
                        default=str,
                    )
                )


class GetPortfolioIdByPortfolioName(tasks.PuppetTask):
    portfolio = luigi.Parameter()
    account_id = luigi.Parameter()
    region = luigi.Parameter()

    def params_for_results_display(self):
        return {
            "account_id": self.account_id,
            "region": self.region,
            "portfolio": self.portfolio,
        }

    def output(self):
        return luigi.LocalTarget(
            f"output/GetPortfolioIdByPortfolioName/"
            f"{self.account_id}-{self.region}-{self.portfolio}.json"
        )

    def run(self):
        with betterboto_client.CrossAccountClientContextManager(
                'servicecatalog',
                f"arn:aws:iam::{self.account_id}:role/servicecatalog-puppet/PuppetRole",
                f"{self.account_id}-{self.region}",
                region_name=self.region
        ) as cross_account_servicecatalog:
            portfolio_id = aws.get_portfolio_id_for(cross_account_servicecatalog, self.portfolio)
            with self.output().open('w') as f:
                f.write(
                    json.dumps(
                        {
                            "portfolio_name": self.portfolio,
                            "portfolio_id": portfolio_id,
                        },
                        indent=4,
                        default=str,
                    )
                )


class ProvisionActionTask(tasks.PuppetTask):
    source = luigi.Parameter()
    phase = luigi.Parameter()
    source_type = luigi.Parameter()
    type = luigi.Parameter()
    name = luigi.Parameter()
    project_name = luigi.Parameter()
    account_id = luigi.Parameter()
    region = luigi.Parameter()
    parameters = luigi.DictParameter()

    def params_for_results_display(self):
        return self.param_kwargs

    @property
    def uid(self):
        return f"{self.__class__.__name__}/{self.type}--{self.source}--{self.phase}--{self.source_type}--{self.name}-" \
               f"-{self.project_name}--{self.account_id}--{self.region}"

    def output(self):
        return luigi.LocalTarget(
            f"output/{self.uid}.json"
        )

    def requires(self):
        ssm_params = {}
        for param_name, param_details in self.parameters.items():
            if param_details.get('ssm'):
                if param_details.get('default'):
                    del param_details['default']
                ssm_params[param_name] = tasks.GetSSMParamTask(
                    parameter_name=param_name,
                    name=param_details.get('ssm').get('name'),
                    region=param_details.get('ssm').get('region', config.get_home_region())
                )
        return {
            'ssm_params': ssm_params,
        }

    def run(self):
        all_params = {}
        for param_name, param_details in self.parameters.items():
            if param_details.get('ssm'):
                with self.input().get('ssm_params').get(param_name).open() as f:
                    all_params[param_name] = json.loads(f.read()).get('Value')
            if param_details.get('default'):
                all_params[param_name] = param_details.get('default')
        logger.info(f"[{self.uid}] :: finished collecting all_params: {all_params}")

        environmentVariablesOverride = [
            {
                'name': param_name, 'value': param_details, 'type': 'PLAINTEXT'
            } for param_name, param_details in all_params.items()
        ]

        role = f"arn:aws:iam::{self.account_id}:role/servicecatalog-puppet/PuppetRole"
        with betterboto_client.CrossAccountClientContextManager(
                'codebuild', role, f'sc-{self.region}-{self.account_id}', region_name=self.region
        ) as codebuild:
            build = codebuild.start_build_and_wait_for_completion(
                projectName=self.project_name,
                environmentVariablesOverride=environmentVariablesOverride,
            )
            if build.get('buildStatus') != 'SUCCEEDED':
                raise Exception(f"{self.uid}: Build failed: {build.get('buildStatus')}")
        self.write_output(self.param_kwargs)


class CreateSpokeLocalPortfolioTask(tasks.PuppetTask):
    account_id = luigi.Parameter()
    region = luigi.Parameter()
    portfolio = luigi.Parameter()
    organization = luigi.Parameter(significant=False)
    pre_actions = luigi.ListParameter(default=[])

    provider_name = luigi.Parameter(significant=False, default='not set')
    description = luigi.Parameter(significant=False, default='not set')

    def requires(self):
        return {
            'pre_actions': [ProvisionActionTask(**p) for p in self.pre_actions]
        }

    def params_for_results_display(self):
        return {
            "account_id": self.account_id,
            "region": self.region,
            "portfolio": self.portfolio,
        }

    @property
    def node_id(self):
        return f"{self.portfolio}_{self.account_id}_{self.region}"

    def graph_node(self):
        label = f"<b>CreatePortfolioInSpoke</b><br/>Portfolio: {self.portfolio}<br/>AccountId: {self.account_id}<br/>Region: {self.region}"
        return f"\"{self.__class__.__name__}_{self.node_id}\" [fillcolor=chocolate style=filled label= < {label} >]"

    def get_graph_lines(self):
        return []

    def output(self):
        return luigi.LocalTarget(
            f"output/CreateSpokeLocalPortfolioTask/"
            f"{self.account_id}-{self.region}-{self.portfolio}.json"
        )

    def run(self):
        logger.info(f"[{self.portfolio}] {self.account_id}:{self.region} :: starting creating portfolio")
        role = f"arn:aws:iam::{self.account_id}:role/servicecatalog-puppet/PuppetRole"
        with betterboto_client.CrossAccountClientContextManager(
                'servicecatalog', role, f'sc-{self.account_id}-{self.region}', region_name=self.region
        ) as spoke_service_catalog:
            spoke_portfolio = aws.ensure_portfolio(
                spoke_service_catalog,
                self.portfolio,
                self.provider_name,
                self.description,
            )
        with self.output().open('w') as f:
            f.write(
                json.dumps(
                    spoke_portfolio,
                    indent=4,
                    default=str,
                )
            )
        logger.info(f"[{self.portfolio}] {self.account_id}:{self.region} :: finished creating portfolio")


class CreateAssociationsForPortfolioTask(tasks.PuppetTask):
    account_id = luigi.Parameter()
    region = luigi.Parameter()
    portfolio = luigi.Parameter()
    puppet_account_id = luigi.Parameter()
    organization = luigi.Parameter()
    pre_actions = luigi.ListParameter(default=[])

    associations = luigi.ListParameter(default=[])
    dependencies = luigi.ListParameter(default=[])

    should_use_sns = luigi.Parameter(significant=False, default=False)

    def requires(self):
        return {
            'create_spoke_local_portfolio_task': CreateSpokeLocalPortfolioTask(
                account_id=self.account_id,
                region=self.region,
                portfolio=self.portfolio,
                organization=self.organization,
                pre_actions=self.pre_actions,
            ),
            'deps': [provisioning.ProvisionProductTask(**dependency) for dependency in self.dependencies]
        }

    @property
    def node_id(self):
        return f"{self.portfolio}_{self.account_id}_{self.region}"

    def graph_node(self):
        label = f"<b>CreateAssociationsForPortfolio</b><br/>Portfolio: {self.portfolio}<br/>AccountId: {self.account_id}<br/>Region: {self.region}"
        return f"\"{self.__class__.__name__}_{self.node_id}\" [fillcolor=turquoise style=filled label= < {label} >]"

    def get_graph_lines(self):
        return [
            f"\"{CreateAssociationsForPortfolioTask.__name__}_{self.node_id}\" -> \"{provisioning.ProvisionProductTask.__name__}_{'_'.join([dep.get('launch_name'), dep.get('portfolio'), dep.get('product'), dep.get('version'), dep.get('account_id'), dep.get('region')])}\""
            for dep in self.dependencies
        ] + [
            f"\"{CreateAssociationsForPortfolioTask.__name__}_{self.node_id}\" -> \"{CreateSpokeLocalPortfolioTask.__name__}_{'_'.join([self.portfolio, self.account_id, self.region])}\""
        ]

    def params_for_results_display(self):
        return {
            "account_id": self.account_id,
            "region": self.region,
            "portfolio": self.portfolio,
        }

    def output(self):
        return luigi.LocalTarget(
            f"output/CreateAssociationsForPortfolioTask/"
            f"{self.account_id}-{self.region}-{self.portfolio}.json"
        )

    def run(self):
        logger.info(f"[{self.portfolio}] {self.account_id}:{self.region} :: starting creating associations")
        role = f"arn:aws:iam::{self.account_id}:role/servicecatalog-puppet/PuppetRole"

        with self.input().get('create_spoke_local_portfolio_task').open('r') as f:
            portfolio_id = json.loads(f.read()).get('Id')
        logger.info(f"[{self.portfolio}] {self.account_id}:{self.region} :: using portfolio_id: {portfolio_id}")

        with betterboto_client.CrossAccountClientContextManager(
                'cloudformation', role, f'cfn-{self.account_id}-{self.region}', region_name=self.region
        ) as cloudformation:
            template = config.env.get_template('associations.template.yaml.j2').render(
                portfolio={
                    'DisplayName': self.portfolio,
                    'Associations': self.associations
                },
                portfolio_id=portfolio_id,
            )
            stack_name = f"associations-for-portfolio-{portfolio_id}"
            cloudformation.create_or_update(
                StackName=stack_name,
                TemplateBody=template,
                NotificationARNs=[
                    f"arn:aws:sns:{self.region}:{self.puppet_account_id}:servicecatalog-puppet-cloudformation-regional-events"
                ] if self.should_use_sns else [],
            )
            result = cloudformation.describe_stacks(
                StackName=stack_name,
            ).get('Stacks')[0]
            with self.output().open('w') as f:
                f.write(
                    json.dumps(
                        result,
                        indent=4,
                        default=str,
                    )
                )
            logger.info(f"[{self.portfolio}] {self.account_id}:{self.region} :: Finished importing")


class ImportIntoSpokeLocalPortfolioTask(tasks.PuppetTask):
    account_id = luigi.Parameter()
    region = luigi.Parameter()
    portfolio = luigi.Parameter()
    organization = luigi.Parameter()
    pre_actions = luigi.ListParameter()
    hub_portfolio_id = luigi.Parameter()
    post_actions = luigi.ListParameter(default=[])

    def requires(self):
        return CreateSpokeLocalPortfolioTask(
            account_id=self.account_id,
            region=self.region,
            portfolio=self.portfolio,
            organization=self.organization,
            pre_actions=self.pre_actions,
        )

    @property
    def node_id(self):
        return f"{self.portfolio}_{self.account_id}_{self.region}"

    def graph_node(self):
        label = f"<b>ImportProductsIntoPortfolio</b><br/>Portfolio: {self.portfolio}<br/>AccountId: {self.account_id}<br/>Region: {self.region}"
        return f"\"{self.__class__.__name__}_{self.node_id}\" [fillcolor=deepskyblue style=filled label= < {label} >]"

    def get_graph_lines(self):
        return [
            f"\"{ImportIntoSpokeLocalPortfolioTask.__name__}_{self.node_id}\" -> \"{CreateSpokeLocalPortfolioTask.__name__}_{self.node_id}\""
        ]

    def params_for_results_display(self):
        return {
            "account_id": self.account_id,
            "region": self.region,
            "portfolio": self.portfolio,
            "hub_portfolio_id": self.hub_portfolio_id,
        }

    def output(self):
        return luigi.LocalTarget(
            f"output/ImportIntoSpokeLocalPortfolioTask/"
            f"{self.account_id}-{self.region}-{self.portfolio}-{self.hub_portfolio_id}.json"
        )

    def run(self):
        logger.info(f"[{self.portfolio}] {self.account_id}:{self.region} :: starting to import into spoke")

        product_name_to_id_dict = {}

        with betterboto_client.ClientContextManager(
                'servicecatalog', region_name=self.region
        ) as service_catalog:
            response = service_catalog.search_products_as_admin_single_page(PortfolioId=self.hub_portfolio_id)
            for product_view_detail in response.get('ProductViewDetails', []):
                spoke_product_id = False
                target_product_id = False
                product_view_summary = product_view_detail.get('ProductViewSummary')
                hub_product_name = product_view_summary.get('Name')
                hub_product_id = product_view_summary.get('ProductId')

                product_versions_that_should_be_copied = {}
                product_versions_that_should_be_updated = {}
                hub_provisioning_artifact_details = service_catalog.list_provisioning_artifacts(
                    ProductId=hub_product_id
                ).get('ProvisioningArtifactDetails', [])
                for hub_provisioning_artifact_detail in hub_provisioning_artifact_details:
                    if hub_provisioning_artifact_detail.get('Type') == 'CLOUD_FORMATION_TEMPLATE':
                        product_versions_that_should_be_copied[
                            f"{hub_provisioning_artifact_detail.get('Name')}"
                        ] = hub_provisioning_artifact_detail
                        product_versions_that_should_be_updated[
                            f"{hub_provisioning_artifact_detail.get('Name')}"
                        ] = hub_provisioning_artifact_detail

                logger.info(f"[{self.portfolio}] {self.account_id}:{self.region} :: Copying {hub_product_name}")
                hub_product_arn = product_view_detail.get('ProductARN')
                copy_args = {
                    'SourceProductArn': hub_product_arn,
                    'CopyOptions': [
                        'CopyTags',
                    ],
                }
                with self.input().open('r') as f:
                    spoke_portfolio = json.loads(f.read())
                portfolio_id = spoke_portfolio.get("Id")

                logger.info(f"[{self.portfolio}] {self.account_id}:{self.region} {hub_product_name} :: searching in "
                            f"spoke for product")
                role = f"arn:aws:iam::{self.account_id}:role/servicecatalog-puppet/PuppetRole"
                with betterboto_client.CrossAccountClientContextManager(
                        'servicecatalog', role, f"sc-{self.account_id}-{self.region}", region_name=self.region
                ) as spoke_service_catalog:
                    p = None
                    try:
                        p = spoke_service_catalog.search_products_as_admin_single_page(
                            PortfolioId=portfolio_id,
                            Filters={'FullTextSearch': [hub_product_name]}
                        )
                    except spoke_service_catalog.exceptions.ResourceNotFoundException as e:
                        logger.info(f"[{self.portfolio}] {self.account_id}:{self.region} {hub_product_name} :: "
                                    f"swallowing exception: {str(e)}")

                    if p is not None:
                        for spoke_product_view_details in p.get('ProductViewDetails'):
                            spoke_product_view = spoke_product_view_details.get('ProductViewSummary')
                            if spoke_product_view.get('Name') == hub_product_name:
                                spoke_product_id = spoke_product_view.get('ProductId')
                                product_name_to_id_dict[hub_product_name] = spoke_product_id
                                copy_args['TargetProductId'] = spoke_product_id
                                spoke_provisioning_artifact_details = spoke_service_catalog.list_provisioning_artifacts(
                                    ProductId=spoke_product_id
                                ).get('ProvisioningArtifactDetails')
                                for provisioning_artifact_detail in spoke_provisioning_artifact_details:
                                    id_to_delete = f"{provisioning_artifact_detail.get('Name')}"
                                    if product_versions_that_should_be_copied.get(id_to_delete, None) is not None:
                                        logger.info(f"[{self.portfolio}] {self.account_id}:{self.region} "
                                                    f"{hub_product_name} :: Going to skip "
                                                    f"{spoke_product_id} "
                                                    f"{provisioning_artifact_detail.get('Name')}"
                                                    )
                                        del product_versions_that_should_be_copied[id_to_delete]

                    if len(product_versions_that_should_be_copied.keys()) == 0:
                        logger.info(f"[{self.portfolio}] {self.account_id}:{self.region} {hub_product_name} :: "
                                    f"no versions to copy")
                    else:
                        logger.info(f"[{self.portfolio}] {self.account_id}:{self.region} {hub_product_name} :: "
                                    f"about to copy product")

                        copy_args['SourceProvisioningArtifactIdentifiers'] = [
                            {'Id': a.get('Id')} for a in product_versions_that_should_be_copied.values()
                        ]

                        logger.info(f"[{self.portfolio}] {self.account_id}:{self.region} :: about to copy product with"
                                    f"args: {copy_args}")
                        copy_product_token = spoke_service_catalog.copy_product(
                            **copy_args
                        ).get('CopyProductToken')
                        while True:
                            time.sleep(5)
                            r = spoke_service_catalog.describe_copy_product_status(
                                CopyProductToken=copy_product_token
                            )
                            target_product_id = r.get('TargetProductId')
                            logger.info(f"[{self.portfolio}] {self.account_id}:{self.region} :: "
                                        f"{hub_product_name} status: {r.get('CopyProductStatus')}")
                            if r.get('CopyProductStatus') == 'FAILED':
                                raise Exception(f"[{self.portfolio}] {self.account_id}:{self.region} :: Copying "
                                                f"{hub_product_name} failed: {r.get('StatusDetail')}")
                            elif r.get('CopyProductStatus') == 'SUCCEEDED':
                                break

                        logger.info(f"[{self.portfolio}] {self.account_id}:{self.region} :: adding {target_product_id} "
                                    f"to portfolio {portfolio_id}")
                        spoke_service_catalog.associate_product_with_portfolio(
                            ProductId=target_product_id,
                            PortfolioId=portfolio_id,
                        )

                        # associate_product_with_portfolio is not a synchronous request
                        logger.info(f"[{self.portfolio}] {self.account_id}:{self.region} :: waiting for adding of "
                                    f"{target_product_id} to portfolio {portfolio_id}")
                        while True:
                            time.sleep(2)
                            response = spoke_service_catalog.search_products_as_admin_single_page(
                                PortfolioId=portfolio_id,
                            )
                            products_ids = [
                                product_view_detail.get('ProductViewSummary').get('ProductId') for product_view_detail
                                in response.get('ProductViewDetails')
                            ]
                            logger.info(f"[{self.portfolio}] {self.account_id}:{self.region} :: Looking for "
                                        f"{target_product_id} in {products_ids}")

                            if target_product_id in products_ids:
                                break

                        product_name_to_id_dict[hub_product_name] = target_product_id

                    product_id_in_spoke = spoke_product_id or target_product_id
                    spoke_provisioning_artifact_details = spoke_service_catalog.list_provisioning_artifacts(
                        ProductId=product_id_in_spoke
                    ).get('ProvisioningArtifactDetails', [])
                    for version_name, version_details in product_versions_that_should_be_updated.items():
                        logging.info(f"{version_name} is active: {version_details.get('Active')} in hub")
                        for spoke_provisioning_artifact_detail in spoke_provisioning_artifact_details:
                            if spoke_provisioning_artifact_detail.get('Name') == version_name:
                                logging.info(
                                    f"Updating active of {version_name}/{spoke_provisioning_artifact_detail.get('Id')} "
                                    f"in the spoke to {version_details.get('Active')}"
                                )
                                spoke_service_catalog.update_provisioning_artifact(
                                    ProductId=product_id_in_spoke,
                                    ProvisioningArtifactId=spoke_provisioning_artifact_detail.get('Id'),
                                    Active=version_details.get('Active'),
                                )

        for p in self.post_actions:
            yield ProvisionActionTask(**p)

        with self.output().open('w') as f:
            f.write(
                json.dumps(
                    {
                        'portfolio': spoke_portfolio,
                        'product_versions_that_should_be_copied': product_versions_that_should_be_copied,
                        'products': product_name_to_id_dict,
                    },
                    indent=4,
                    default=str,
                )
            )
        logger.info(f"[{self.portfolio}] {self.account_id}:{self.region} :: Finished importing")


class CreateLaunchRoleConstraintsForPortfolio(tasks.PuppetTask):
    account_id = luigi.Parameter()
    region = luigi.Parameter()
    portfolio = luigi.Parameter()
    hub_portfolio_id = luigi.Parameter()
    puppet_account_id = luigi.Parameter()
    organization = luigi.Parameter()

    launch_constraints = luigi.DictParameter()

    dependencies = luigi.ListParameter(default=[])

    post_actions = luigi.ListParameter()
    pre_actions = luigi.ListParameter()

    should_use_sns = luigi.Parameter(default=False, significant=False)

    def requires(self):
        return {
            'create_spoke_local_portfolio_task': ImportIntoSpokeLocalPortfolioTask(
                account_id=self.account_id,
                region=self.region,
                portfolio=self.portfolio,
                organization=self.organization,
                hub_portfolio_id=self.hub_portfolio_id,
                pre_actions=self.pre_actions,
                post_actions=self.post_actions,
            ),
            'deps': [provisioning.ProvisionProductTask(**dependency) for dependency in self.dependencies]
        }

    @property
    def node_id(self):
        return f"{self.portfolio}_{self.account_id}_{self.region}"

    def graph_node(self):
        label = f"<b>CreateLaunchRoleConstraintsForPortfolio</b><br/>Portfolio: {self.portfolio}<br/>AccountId: {self.account_id}<br/>Region: {self.region}"
        return f"\"{self.__class__.__name__}_{self.node_id}\" [fillcolor=orange style=filled label= < {label} >]"

    def get_graph_lines(self):
        return [
            f"\"{CreateLaunchRoleConstraintsForPortfolio.__name__}_{self.node_id}\" -> \"{provisioning.ProvisionProductTask.__name__}_{'_'.join([dep.get('launch_name'), dep.get('portfolio'), dep.get('product'), dep.get('version'), dep.get('account_id'), dep.get('region')])}\""
            for dep in self.dependencies
        ] + [
            f"\"{CreateLaunchRoleConstraintsForPortfolio.__name__}_{self.node_id}\" -> \"{ImportIntoSpokeLocalPortfolioTask.__name__}_{self.node_id}\""
        ]

    def run(self):
        logger.info(f"[{self.portfolio}] {self.account_id}:{self.region} :: Creating launch role constraints for "
                    f"{self.hub_portfolio_id}")
        role = f"arn:aws:iam::{self.account_id}:role/servicecatalog-puppet/PuppetRole"
        with self.input().get('create_spoke_local_portfolio_task').open('r') as f:
            dependency_output = json.loads(f.read())
        spoke_portfolio = dependency_output.get('portfolio')
        portfolio_id = spoke_portfolio.get('Id')
        product_name_to_id_dict = dependency_output.get('products')
        with betterboto_client.CrossAccountClientContextManager(
                'cloudformation', role, f'cfn-{self.account_id}-{self.region}', region_name=self.region
        ) as cloudformation:
            new_launch_constraints = []
            for launch_constraint in self.launch_constraints:
                new_launch_constraint = {
                    'products': [],
                    'roles': launch_constraint.get('roles')
                }
                if launch_constraint.get('products', None) is not None:
                    if isinstance(launch_constraint.get('products'), tuple):
                        new_launch_constraint['products'] += launch_constraint.get('products')
                    elif isinstance(launch_constraint.get('products'), str):
                        with betterboto_client.CrossAccountClientContextManager(
                                'servicecatalog', role, f'sc-{self.account_id}-{self.region}', region_name=self.region
                        ) as service_catalog:
                            response = service_catalog.search_products_as_admin_single_page(PortfolioId=portfolio_id)
                            logger.info(f"response is {response}")
                            for product_view_details in response.get('ProductViewDetails', []):
                                product_view_summary = product_view_details.get('ProductViewSummary')
                                product_name_to_id_dict[product_view_summary.get('Name')] = product_view_summary.get(
                                    'ProductId')
                                if re.match(launch_constraint.get('products'), product_view_summary.get('Name')):
                                    new_launch_constraint['products'].append(product_view_summary.get('Name'))

                if launch_constraint.get('product', None) is not None:
                    new_launch_constraint['products'].append(launch_constraint.get('product'))

                new_launch_constraints.append(new_launch_constraint)

            template = config.env.get_template('launch_role_constraints.template.yaml.j2').render(
                portfolio={
                    'DisplayName': self.portfolio,
                },
                portfolio_id=portfolio_id,
                launch_constraints=new_launch_constraints,
                product_name_to_id_dict=product_name_to_id_dict,
            )
            # time.sleep(30)
            stack_name_v1 = f"launch-constraints-for-portfolio-{portfolio_id}"
            cloudformation.ensure_deleted(
                StackName=stack_name_v1,
            )
            stack_name_v2 = f"launch-constraints-v2-for-portfolio-{portfolio_id}"
            cloudformation.create_or_update(
                StackName=stack_name_v2,
                TemplateBody=template,
                NotificationARNs=[
                    f"arn:aws:sns:{self.region}:{self.puppet_account_id}:servicecatalog-puppet-cloudformation-regional-events"
                ] if self.should_use_sns else [],
            )
            result = cloudformation.describe_stacks(
                StackName=stack_name_v2,
            ).get('Stacks')[0]
            with self.output().open('w') as f:
                f.write(
                    json.dumps(
                        result,
                        indent=4,
                        default=str,
                    )
                )

            for p in self.post_actions:
                yield ProvisionActionTask(**p)

    def params_for_results_display(self):
        return {
            "account_id": self.account_id,
            "region": self.region,
            "portfolio": self.portfolio,
            "hub_portfolio_id": self.hub_portfolio_id,
        }

    def output(self):
        return luigi.LocalTarget(
            f"output/CreateLaunchRoleConstraintsForPortfolio/"
            f"{self.account_id}-{self.region}-{self.portfolio}-{self.hub_portfolio_id}.json"
        )


class RequestPolicyTask(tasks.PuppetTask):
    type = luigi.Parameter()
    region = luigi.Parameter()
    account_id = luigi.Parameter()
    organization = luigi.Parameter(default=None)

    @property
    def uid(self):
        return f"{self.__class__.__name__}/{self.account_id}--{self.region}"

    def output(self):
        return luigi.LocalTarget(
            f"output/{self.uid}.json"
        )

    def run(self):
        if self.organization is not None:
            p = f'data/{self.type}/{self.region}/organizations/'
            if not os.path.exists(p):
                os.makedirs(p, exist_ok=True)
            path = f'{p}/{self.organization}.json'
        else:
            p = f'data/{self.type}/{self.region}/accounts/'
            if not os.path.exists(p):
                os.makedirs(p, exist_ok=True)
            path = f'{p}/{self.account_id}.json'

        f = open(path, 'w')
        f.write(
            json.dumps(
                self.param_kwargs,
                indent=4,
                default=str,
            )
        )
        f.close()
        self.write_output(self.param_kwargs)


class ShareAndAcceptPortfolioTask(tasks.PuppetTask):
    account_id = luigi.Parameter()
    region = luigi.Parameter()
    portfolio = luigi.Parameter()
    puppet_account_id = luigi.Parameter()

    @property
    def resources(self):
        return {
            f"{self.puppet_account_id}-{self.region}-{self.portfolio}": 1
        }

    @property
    def uid(self):
        return f"{self.__class__.__name__}/{self.account_id}--{self.region}--{self.portfolio}"

    def output(self):
        return luigi.LocalTarget(
            f"output/{self.uid}.json"
        )

    def run(self):
        logger.info(f"{self.uid} starting ShareAndAcceptPortfolioTask")
        portfolio_id = aws.get_portfolio_for(self.portfolio, self.puppet_account_id, self.region).get('Id')
        p = f'data/shares/{self.region}/{self.portfolio}/'
        if not os.path.exists(p):
            os.makedirs(p, exist_ok=True)
        path = f'{p}/{self.account_id}.json'
        with open(path, 'w') as f:
            f.write("{}")

        logging.info(f"{self.uid}: checking {portfolio_id} with {self.account_id}")

        with betterboto_client.ClientContextManager('servicecatalog', region_name=self.region) as servicecatalog:
            account_ids = servicecatalog.list_portfolio_access(PortfolioId=portfolio_id).get('AccountIds')

            if self.account_id in account_ids:
                logging.info(f"{self.uid}: not sharing {portfolio_id} with {self.account_id} as was previously shared")
            else:
                logging.info(f"{self.uid}: sharing {portfolio_id} with {self.account_id}")

            with betterboto_client.CrossAccountClientContextManager(
                    'servicecatalog',
                    f"arn:aws:iam::{self.account_id}:role/servicecatalog-puppet/PuppetRole",
                    f"{self.account_id}-{self.region}-PuppetRole",
                    region_name=self.region,
            ) as cross_account_servicecatalog:
                was_accepted = False
                accepted_portfolio_shares = cross_account_servicecatalog.list_accepted_portfolio_shares_single_page().get(
                    'PortfolioDetails'
                )
                for accepted_portfolio_share in accepted_portfolio_shares:
                    if accepted_portfolio_share.get('Id') == portfolio_id:
                        was_accepted = True
                        break
                if not was_accepted:
                    logging.info(f"{self.uid}: accepting {portfolio_id}")
                    cross_account_servicecatalog.accept_portfolio_share(
                        PortfolioId=portfolio_id,
                    )

                principals_for_portfolio = cross_account_servicecatalog.list_principals_for_portfolio_single_page(
                    PortfolioId=portfolio_id
                ).get('Principals')
                principal_was_associated = False
                principal_to_associate = f"arn:aws:iam::{self.account_id}:role/servicecatalog-puppet/PuppetRole"
                for principal_for_portfolio in principals_for_portfolio:
                    if principal_for_portfolio.get('PrincipalARN') == principal_to_associate:
                        principal_was_associated = True

                if not principal_was_associated:
                    cross_account_servicecatalog.associate_principal_with_portfolio(
                        PortfolioId=portfolio_id,
                        PrincipalARN=principal_to_associate,
                        PrincipalType='IAM',
                    )

        self.write_output(self.param_kwargs)


class CreateAssociationsInPythonForPortfolioTask(tasks.PuppetTask):
    account_id = luigi.Parameter()
    region = luigi.Parameter()
    portfolio = luigi.Parameter()

    @property
    def resources(self):
        return {
            f"{self.region}-{self.portfolio}": 1
        }

    @property
    def uid(self):
        return f"{self.__class__.__name__}/{self.account_id}--{self.region}--{self.portfolio}"

    def output(self):
        return luigi.LocalTarget(
            f"output/{self.uid}.json"
        )

    def run(self):
        p = f'data/associations/{self.region}/{self.portfolio}/'
        if not os.path.exists(p):
            os.makedirs(p, exist_ok=True)
        path = f'{p}/{self.account_id}.json'
        with open(path, 'w') as f:
            f.write("{}")

        portfolio_id = aws.get_portfolio_for(self.portfolio, self.account_id, self.region).get('Id')
        logging.info(f"{self.uid}: Creating the association for portfolio {portfolio_id}")
        with betterboto_client.ClientContextManager('servicecatalog', region_name=self.region) as servicecatalog:
            servicecatalog.associate_principal_with_portfolio(
                PortfolioId=portfolio_id,
                PrincipalARN=f"arn:aws:iam::{self.account_id}:role/servicecatalog-puppet/PuppetRole",
                PrincipalType='IAM'
            )
        self.write_output(self.param_kwargs)


class CreateShareForAccountLaunchRegion(tasks.PuppetTask):
    """for the given account_id and launch and region create the shares"""
    puppet_account_id = luigi.Parameter()
    account_id = luigi.Parameter()
    region = luigi.Parameter()
    portfolio = luigi.Parameter()
    expanded_from = luigi.Parameter()
    organization = luigi.Parameter()

    @property
    def uid(self):
        return f"{self.__class__.__name__}/{self.account_id}--{self.portfolio}--{self.region}"

    def output(self):
        return luigi.LocalTarget(
            f"output/{self.uid}.json"
        )

    def requires(self):
        logging.info(f"{self.uid}: expanded_from = {self.expanded_from}")
        deps = {
            'topic': RequestPolicyTask(
                type="topic",
                region=self.region,
                organization=self.organization,
                account_id=self.account_id,
            ),
            'bucket': RequestPolicyTask(
                type="bucket",
                region=self.region,
                organization=self.organization,
                account_id=self.account_id,
            ),
        }

        if self.account_id == self.puppet_account_id:
            # create an association
            deps['share'] = CreateAssociationsInPythonForPortfolioTask(
                self.account_id,
                self.region,
                self.portfolio,
            )
        else:
            deps['share'] = ShareAndAcceptPortfolioTask(
                self.account_id,
                self.region,
                self.portfolio,
                self.puppet_account_id,
            )
        return deps

    def run(self):
        self.write_output(self.param_kwargs)
