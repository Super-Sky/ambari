#!/usr/bin/env python
"""
Licensed to the Apache Software Foundation (ASF) under one
or more contributor license agreements.  See the NOTICE file
distributed with this work for additional information
regarding copyright ownership.  The ASF licenses this file
to you under the Apache License, Version 2.0 (the
"License"); you may not use this file except in compliance
with the License.  You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.

"""

import glob
import os
from resource_management.core.resources import Directory
from resource_management.core.resources.system import Execute, File
from resource_management.core.source import InlineTemplate
from resource_management.core import sudo
from resource_management.core.logger import Logger
from resource_management.core.source import StaticFile
from resource_management.libraries import XmlConfig
from resource_management.libraries.functions.check_process_status import check_process_status
from resource_management.libraries.functions.format import format
from resource_management.libraries.functions import conf_select
from resource_management.libraries.functions import stack_select
from resource_management.libraries.functions import StackFeature
from resource_management.libraries.functions.stack_features import check_stack_feature
from resource_management.libraries.functions.version import format_stack_version
from resource_management.libraries.script.script import Script

class Master(Script):

  def get_component_name(self):
    return "zeppelin-server"

  def install(self, env):
    import params
    env.set_params(params)
    self.install_packages(env)

    # create the pid and zeppelin dirs
    Directory([params.zeppelin_pid_dir, params.zeppelin_dir],
              owner=params.zeppelin_user,
              group=params.zeppelin_group,
              cd_access="a",
              create_parents=True,
              mode=0755
              )

    # update the configs specified by user
    self.configure(env)

    Execute('echo spark_version:' + params.spark_version + ' detected for spark_home: '
            + params.spark_home + ' >> ' + params.zeppelin_log_file, user=params.zeppelin_user)

  def create_zeppelin_dir(self, params):
    params.HdfsResource(format("/user/{zeppelin_user}"),
                        type="directory",
                        action="create_on_execute",
                        owner=params.zeppelin_user,
                        recursive_chown=True,
                        recursive_chmod=True
                        )
    params.HdfsResource(format("/user/{zeppelin_user}/test"),
                        type="directory",
                        action="create_on_execute",
                        owner=params.zeppelin_user,
                        recursive_chown=True,
                        recursive_chmod=True
                        )
    params.HdfsResource(format("/apps/zeppelin"),
                        type="directory",
                        action="create_on_execute",
                        owner=params.zeppelin_user,
                        recursive_chown=True,
                        recursive_chmod=True
                        )

    spark_deps_full_path = glob.glob(params.zeppelin_dir + '/interpreter/spark/dep/zeppelin-spark-dependencies-*.jar')[0]
    spark_dep_file_name = os.path.basename(spark_deps_full_path);

    params.HdfsResource(params.spark_jar_dir + "/" + spark_dep_file_name,
                        type="file",
                        action="create_on_execute",
                        source=spark_deps_full_path,
                        group=params.zeppelin_group,
                        owner=params.zeppelin_user,
                        mode=0444,
                        replace_existing_files=True,
                        )

    params.HdfsResource(None, action="execute")

  def create_zeppelin_log_dir(self, env):
    import params
    env.set_params(params)
    Directory([params.zeppelin_log_dir],
              owner=params.zeppelin_user,
              group=params.zeppelin_group,
              cd_access="a",
              create_parents=True,
              mode=0755
              )

  def configure(self, env):
    import params
    import status_params
    env.set_params(params)
    env.set_params(status_params)
    self.create_zeppelin_log_dir(env)

    # write out zeppelin-site.xml
    XmlConfig("zeppelin-site.xml",
              conf_dir=params.conf_dir,
              configurations=params.config['configurations']['zeppelin-config'],
              owner=params.zeppelin_user,
              group=params.zeppelin_group
              )
    # write out zeppelin-env.sh
    env_content = InlineTemplate(params.zeppelin_env_content)
    File(format("{params.conf_dir}/zeppelin-env.sh"), content=env_content,
         owner=params.zeppelin_user, group=params.zeppelin_group)

    # write out shiro.ini
    shiro_ini_content = InlineTemplate(params.shiro_ini_content)
    File(format("{params.conf_dir}/shiro.ini"), content=shiro_ini_content,
         owner=params.zeppelin_user, group=params.zeppelin_group)

    # write out log4j.properties
    File(format("{params.conf_dir}/log4j.properties"), content=params.log4j_properties_content,
         owner=params.zeppelin_user, group=params.zeppelin_group)

    # copy hive-site.xml
    File(format("{params.conf_dir}/hive-site.xml"), content=StaticFile("/etc/spark/conf/hive-site.xml"),
         owner=params.zeppelin_user, group=params.zeppelin_group)

    if len(params.hbase_master_hosts) > 0:
      # copy hbase-site.xml
      File(format("{params.conf_dir}/hbase-site.xml"), content=StaticFile("/etc/hbase/conf/hbase-site.xml"),
           owner=params.zeppelin_user, group=params.zeppelin_group)

  def stop(self, env, upgrade_type=None):
    import params
    self.create_zeppelin_log_dir(env)
    Execute(params.zeppelin_dir + '/bin/zeppelin-daemon.sh stop >> ' + params.zeppelin_log_file,
            user=params.zeppelin_user)

  def start(self, env, upgrade_type=None):
    import params
    import status_params
    import time
    self.configure(env)

    Execute(("chown", "-R", format("{zeppelin_user}") + ":" + format("{zeppelin_group}"), "/etc/zeppelin"),
            sudo=True)
    Execute(("chown", "-R", format("{zeppelin_user}") + ":" + format("{zeppelin_group}"),
             os.path.join(params.zeppelin_dir, "notebook")), sudo=True)

    if params.security_enabled:
        zeppelin_kinit_cmd = format("{kinit_path_local} -kt {zeppelin_kerberos_keytab} {zeppelin_kerberos_principal}; ")
        Execute(zeppelin_kinit_cmd, user=params.zeppelin_user)

    if glob.glob(
            params.zeppelin_dir + '/interpreter/spark/dep/zeppelin-spark-dependencies-*.jar') and os.path.exists(
      glob.glob(params.zeppelin_dir + '/interpreter/spark/dep/zeppelin-spark-dependencies-*.jar')[0]):
      self.create_zeppelin_dir(params)

    # if first_setup:
    if not glob.glob(params.conf_dir + "/interpreter.json") and \
      not os.path.exists(params.conf_dir + "/interpreter.json"):
      Execute(params.zeppelin_dir + '/bin/zeppelin-daemon.sh start >> '
              + params.zeppelin_log_file, user=params.zeppelin_user)
      self.check_zeppelin_server()
      self.update_zeppelin_interpreter()

    self.update_kerberos_properties()

    Execute(params.zeppelin_dir + '/bin/zeppelin-daemon.sh restart >> '
            + params.zeppelin_log_file, user=params.zeppelin_user)
    pidfile = glob.glob(os.path.join(status_params.zeppelin_pid_dir,
                                     'zeppelin-' + params.zeppelin_user + '*.pid'))[0]
    Logger.info(format("Pid file is: {pidfile}"))

  def status(self, env):
    import status_params
    env.set_params(status_params)

    try:
        pid_file = glob.glob(status_params.zeppelin_pid_dir + '/zeppelin-' +
                             status_params.zeppelin_user + '*.pid')[0]
    except IndexError:
        pid_file = ''
    check_process_status(pid_file)

  def get_interpreter_settings(self):
    import params
    import json

    interpreter_config = os.path.join(params.conf_dir, "interpreter.json")
    config_content = sudo.read_file(interpreter_config)
    config_data = json.loads(config_content)
    return config_data

  def pre_upgrade_restart(self, env, upgrade_type=None):
    Logger.info("Executing Stack Upgrade pre-restart")
    import params
    env.set_params(params)

    if params.version and check_stack_feature(StackFeature.ROLLING_UPGRADE, format_stack_version(params.version)):
      conf_select.select(params.stack_name, "zeppelin", params.version)
      stack_select.select("zeppelin-server", params.version)

  def set_interpreter_settings(self, config_data):
    import params
    import json

    interpreter_config = os.path.join(params.conf_dir, "interpreter.json")
    File(interpreter_config,
         group=params.zeppelin_group,
         owner=params.zeppelin_user,
         content=json.dumps(config_data, indent=2)
         )

  def update_kerberos_properties(self):
    import params
    config_data = self.get_interpreter_settings()
    interpreter_settings = config_data['interpreterSettings']
    for notebooks in interpreter_settings:
      notebook = interpreter_settings[notebooks]
      if notebook['group'] == 'livy' and params.livy_livyserver_host:
        if params.zeppelin_kerberos_principal and params.zeppelin_kerberos_keytab and params.security_enabled:
          notebook['properties']['zeppelin.livy.principal'] = params.zeppelin_kerberos_principal
          notebook['properties']['zeppelin.livy.keytab'] = params.zeppelin_kerberos_keytab
        else:
          notebook['properties']['zeppelin.livy.principal'] = ""
          notebook['properties']['zeppelin.livy.keytab'] = ""
      elif notebook['group'] == 'spark':
        if params.zeppelin_kerberos_principal and params.zeppelin_kerberos_keytab and params.security_enabled:
          notebook['properties']['spark.yarn.principal'] = params.zeppelin_kerberos_principal
          notebook['properties']['spark.yarn.keytab'] = params.zeppelin_kerberos_keytab
        else:
          notebook['properties']['spark.yarn.principal'] = ""
          notebook['properties']['spark.yarn.keytab'] = ""
      elif notebook['group'] == 'jdbc':
        if params.zeppelin_kerberos_principal and params.zeppelin_kerberos_keytab and params.security_enabled:
          notebook['properties']['zeppelin.jdbc.auth.type'] = "KERBEROS"
          notebook['properties']['zeppelin.jdbc.principal'] = params.zeppelin_kerberos_principal
          notebook['properties']['zeppelin.jdbc.keytab.location'] = params.zeppelin_kerberos_keytab
        else:
          notebook['properties']['zeppelin.jdbc.auth.type'] = ""
          notebook['properties']['zeppelin.jdbc.principal'] = ""
          notebook['properties']['zeppelin.jdbc.keytab.location'] = ""
      elif notebook['group'] == 'sh':
        if params.zeppelin_kerberos_principal and params.zeppelin_kerberos_keytab and params.security_enabled:
          notebook['properties']['zeppelin.shell.auth.type'] = "KERBEROS"
          notebook['properties']['zeppelin.shell.principal'] = params.zeppelin_kerberos_principal
          notebook['properties']['zeppelin.shell.keytab.location'] = params.zeppelin_kerberos_keytab
        else:
          notebook['properties']['zeppelin.shell.auth.type'] = ""
          notebook['properties']['zeppelin.shell.principal'] = ""
          notebook['properties']['zeppelin.shell.keytab.location'] = ""

    self.set_interpreter_settings(config_data)

  def update_zeppelin_interpreter(self):
    import params
    config_data = self.get_interpreter_settings()
    interpreter_settings = config_data['interpreterSettings']

    for notebooks in interpreter_settings:
      notebook = interpreter_settings[notebooks]
      if notebook['group'] == 'jdbc':
        notebook['dependencies'] = []
        if params.hive_server_host:
          if params.hive_server2_support_dynamic_service_discovery:
            notebook['properties']['hive.url'] = 'jdbc:hive2://' + \
                                                 params.hive_zookeeper_quorum + \
                                                 '/;' + 'serviceDiscoveryMode=zooKeeper;zooKeeperNamespace=hiveserver2'
          else:
            notebook['properties']['hive.url'] = 'jdbc:hive2://' + \
                                                 params.hive_server_host + \
                                                     ':' + params.hive_server_port
          notebook['dependencies'].append(
              {"groupArtifactVersion": "org.apache.hive:hive-jdbc:2.0.1", "local": "false"})
          notebook['dependencies'].append(
              {"groupArtifactVersion": "org.apache.hadoop:hadoop-common:2.7.2", "local": "false"})
          notebook['dependencies'].append(
              {"groupArtifactVersion": "org.apache.hive.shims:hive-shims-0.23:2.1.0", "local": "false"})

        if params.zookeeper_znode_parent \
                and params.hbase_zookeeper_quorum:
            notebook['properties']['phoenix.url'] = "jdbc:phoenix:" + \
                                                    params.hbase_zookeeper_quorum + ':' + \
                                                    params.zookeeper_znode_parent
            notebook['dependencies'].append(
                {"groupArtifactVersion": "org.apache.phoenix:phoenix-core:4.7.0-HBase-1.1", "local": "false"})
      elif notebook['group'] == 'livy' and params.livy_livyserver_host:
        notebook['properties']['livy.spark.master'] = "yarn-cluster"
        notebook['properties']['zeppelin.livy.url'] = "http://" + params.livy_livyserver_host +\
                                                      ":" + params.livy_livyserver_port
      elif notebook['group'] == 'spark':
        notebook['properties']['master'] = "yarn-client"
    self.set_interpreter_settings(config_data)

  def check_zeppelin_server(self, retries=10):
    import params
    import time
    path = params.conf_dir + "/interpreter.json"
    if os.path.exists(path) and os.path.getsize(path):
      Logger.info("interpreter.json found. Zeppelin server started.")
      return True
    else:
      if retries > 0:
        Logger.info("interpreter.json not found. waiting for zeppelin server to start...")
        time.sleep(5)
        self.check_zeppelin_server(retries - 1)
      else:
        return False
    return False

if __name__ == "__main__":
  Master().execute()
