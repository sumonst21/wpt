from __future__ import print_function

import copy
import os, sys, json
import spec_validator
import argparse
import util
import traceback

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'scope'))
import policy_deliveries

def expand_pattern(expansion_pattern, test_expansion_schema):
    expansion = {}
    for artifact_key in expansion_pattern:
        artifact_value = expansion_pattern[artifact_key]
        if artifact_value == '*':
            expansion[artifact_key] = test_expansion_schema[artifact_key]
        elif isinstance(artifact_value, list):
            expansion[artifact_key] = artifact_value
        elif isinstance(artifact_value, dict):
            # Flattened expansion.
            expansion[artifact_key] = []
            values_dict = expand_pattern(artifact_value,
                                         test_expansion_schema[artifact_key])
            for sub_key in values_dict.keys():
                expansion[artifact_key] += values_dict[sub_key]
        else:
            expansion[artifact_key] = [artifact_value]

    return expansion


def permute_expansion(expansion, artifact_order, selection = {}, artifact_index = 0):
    assert isinstance(artifact_order, list), "artifact_order should be a list"

    if artifact_index >= len(artifact_order):
        yield selection
        return

    artifact_key = artifact_order[artifact_index]

    for artifact_value in expansion[artifact_key]:
        selection[artifact_key] = artifact_value
        for next_selection in permute_expansion(expansion,
                                                artifact_order,
                                                selection,
                                                artifact_index + 1):
            yield next_selection


def apply_delivery(source_context_type, delivery_method, delivery_key, delivery_value, x):
  ret = []
  for policy_delivery in x:
    if type(policy_delivery) == unicode and policy_delivery == "policy":
      if source_context_type == 'subresource':
        if delivery_method not in ['attr', 'rel-noref']:
          return None
      elif source_context_type == 'srcdoc':
        if delivery_method not in ['meta']:
          return None
      else:
        if delivery_method not in ['http-rp', 'meta']:
          return None
      y = {"deliveryType": delivery_method, "key": delivery_key, "value": delivery_value}
    elif type(policy_delivery) == unicode and policy_delivery == "anotherPolicy":
      another_delivery_value = "unsafe-url" if delivery_value == "no-referrer" else "no-referrer"
      y = {"deliveryType": "meta", "key": delivery_key, "value": another_delivery_value}
    else:
      y = policy_delivery
    assert "deliveryType" in y, json.dumps(y)
    assert "key" in y, json.dumps(y)
    assert "value" in y, json.dumps(y)
    ret.append(y)
  return ret

def generate_selection(source_context_schema, config, selection, spec, test_html_template_basename):
    source_context = source_context_schema[selection['source_context']]
    delivery_method = selection['delivery_method']
    source_context_list = []
    subresource_delivery_policy = []
    if 'delivery_value' in selection:
      for x in source_context["sourceContextList"]:
        if "policyDeliveries" in x:
          x = copy.deepcopy(x)
          source_context_type = None
          if 'sourceContextType' in x:
            source_context_type = x['sourceContextType']
          x["policyDeliveries"] = apply_delivery(source_context_type, delivery_method, 'referrerPolicy', selection['delivery_value'], x["policyDeliveries"])
          if x["policyDeliveries"] is None:
            return
        source_context_list.append(x)
      subresource_delivery_policy = apply_delivery('subresource', delivery_method, 'referrerPolicy', selection['delivery_value'], source_context["subresourcePolicyDeliveries"])
      if subresource_delivery_policy is None:
        return

    top_source_context = source_context_list.pop(0)

    selection['source_context_list'] = source_context_list
    selection['subresource_policy_deliveries'] = subresource_delivery_policy

    test_parameters = json.dumps(selection, indent=2, separators=(',', ':'))
    # Adjust the template for the test invoking JS. Indent it to look nice.
    indent = "\n" + " " * 8
    test_parameters = test_parameters.replace("\n", indent)

    selection['test_js'] = '''
      %s(
        %s,
        document.querySelector("meta[name=assert]").content,
        new SanityChecker()
      ).start();
      ''' % (config.test_case_name, test_parameters)

    selection['spec_name'] = spec['name']
    selection['test_page_title'] = config.test_page_title_template % spec['title']
    selection['spec_description'] = spec['description']
    selection['spec_specification_url'] = spec['specification_url']
    selection['helper_js'] = config.helper_js
    selection['sanity_checker_js'] = config.sanity_checker_js
    selection['spec_json_js'] = config.spec_json_js

    if selection['delivery_value'] is None:
      selection['delivery_value'] = 'unset'

    test_filename = os.path.join(config.spec_directory, config.test_file_path_pattern % selection)
    test_headers_filename = test_filename + ".headers"
    test_directory = os.path.dirname(test_filename)

    test_html_template = util.get_template(test_html_template_basename)
    disclaimer_template = util.get_template('disclaimer.template')

    html_template_filename = os.path.join(util.template_directory,
                                          test_html_template_basename)
    generated_disclaimer = disclaimer_template \
        % {'generating_script_filename': os.path.relpath(__file__,
                                                         util.test_root_directory),
           'html_template_filename': os.path.relpath(html_template_filename,
                                                     util.test_root_directory)}

    # Adjust the template for the test invoking JS. Indent it to look nice.
    selection['generated_disclaimer'] = generated_disclaimer.rstrip()
    selection['test_description'] = config.test_description_template % selection
    selection['test_description'] = \
        selection['test_description'].rstrip().replace("\n", "\n" + " " * 33)

    # Directory for the test files.
    try:
        os.makedirs(test_directory)
    except:
        pass

    top_policy_deliveries = []
    if 'policyDeliveries' in top_source_context:
      top_policy_deliveries = top_source_context['policyDeliveries']
    top_deliveries = policy_deliveries.handle_deliveries(top_policy_deliveries)

    # Errors in handle_deliveries() indicates e.g. deliveryType is not
    # supported in given context, e.g. http-rp in srcdoc iframe.
    if top_deliveries['error'] != '':
      return

    if len(top_deliveries['headers']) > 0:
        with open(test_headers_filename, "w") as f:
            for header in top_deliveries['headers']:
                f.write('%s: %s\n' % (header, top_deliveries['headers'][header]))

    selection['meta_delivery_method'] = top_deliveries['meta']
    # Obey the lint and pretty format.
    if len(selection['meta_delivery_method']) > 0:
        selection['meta_delivery_method'] = "\n    " + \
                                            selection['meta_delivery_method']

    # Write out the generated HTML file.
    util.write_file(test_filename, test_html_template % selection)


def generate_test_source_files(config, spec_json, target):
    test_expansion_schema = spec_json['test_expansion_schema']
    specification = spec_json['specification']
    source_context_schema = spec_json['source_context_schema']

    spec_json_js_template = util.get_template('spec_json.js.template')
    generated_spec_json_filename = os.path.join(config.spec_directory, "spec_json.js")
    util.write_file(generated_spec_json_filename,
               spec_json_js_template % {'spec_json': json.dumps(spec_json)})

    # Choose a debug/release template depending on the target.
    html_template = "test.%s.html.template" % target

    artifact_order = test_expansion_schema.keys() + ['name']
    artifact_order.remove('expansion')

    # Create list of excluded tests.
    exclusion_dict = {}
    for excluded_pattern in spec_json['excluded_tests']:
        excluded_expansion = \
            expand_pattern(excluded_pattern, test_expansion_schema)
        for excluded_selection in permute_expansion(excluded_expansion,
                                                    artifact_order):
            excluded_selection_path = config.selection_pattern % excluded_selection
            exclusion_dict[excluded_selection_path] = True

    for spec in specification:
        # Used to make entries with expansion="override" override preceding
        # entries with the same |selection_path|.
        output_dict = {}

        for expansion_pattern in spec['test_expansion']:
            expansion = expand_pattern(expansion_pattern, test_expansion_schema)
            for selection in permute_expansion(expansion, artifact_order):
                selection_path = config.selection_pattern % selection
                if not selection_path in exclusion_dict:
                    if selection_path in output_dict:
                        if expansion_pattern['expansion'] != 'override':
                            print("Error: %s's expansion is default but overrides %s" % (selection['name'], output_dict[selection_path]['name']))
                            sys.exit(1)
                    output_dict[selection_path] = copy.deepcopy(selection)
                else:
                    print('Excluding selection:', selection_path)

        for selection_path in output_dict:
            selection = output_dict[selection_path]
            generate_selection(source_context_schema,
                               config,
                               selection,
                               spec,
                               html_template)


def main(config):
    parser = argparse.ArgumentParser(description='Test suite generator utility')
    parser.add_argument('-t', '--target', type = str,
        choices = ("release", "debug"), default = "release",
        help = 'Sets the appropriate template for generating tests')
    parser.add_argument('-s', '--spec', type = str, default = None,
        help = 'Specify a file used for describing and generating the tests')
    # TODO(kristijanburnik): Add option for the spec_json file.
    args = parser.parse_args()

    if args.spec:
      config.spec_directory = args.spec

    spec_filename = os.path.join(config.spec_directory, "spec.src.json")
    spec_json = util.load_spec_json(spec_filename)
    spec_validator.assert_valid_spec_json(spec_json)

    generate_test_source_files(config, spec_json, args.target)