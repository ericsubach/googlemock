#!/usr/bin/env python
#
# Copyright 2008 Google Inc.  All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

################################################################################
# Modifications:
# - Expanded capability to generate a partial mock (author: Eric Subach)
#
# Copyright 2013 In-Depth Engineering. All Rights Reserved.
################################################################################

"""Generate Google Mock classes from base classes.

This program will read in a C++ source file and output the Google Mock
classes for the specified classes.  If no class is specified, all
classes in the source file are emitted.

Usage:
  gmock_class.py [--partial | -p] header-file.h [ClassName]...

Output is sent to stdout.
"""

__author__ = 'nnorwitz@google.com (Neal Norwitz)'

import os
import re
import sys

from cpp import ast
from cpp import utils

from optparse import OptionParser

# Preserve compatibility with Python 2.3.
try:
  _dummy = set
except NameError:
  import sets
  set = sets.Set

_VERSION = (1, 0, 1)  # The version of this script.
# How many spaces to indent.  Can set me with the INDENT environment variable.
_INDENT = 2


def _CreateArgs(node, source):
  args = ''
  if node.parameters:
    # Get the full text of the parameters from the start
    # of the first parameter to the end of the last parameter.
    start = node.parameters[0].start
    end = node.parameters[-1].end
    # Remove // comments.
    args_strings = re.sub(r'//.*', '', source[start:end])
    # Condense multiple spaces and eliminate newlines putting the
    # parameters together on a single line.  Ensure there is a
    # space in an argument which is split by a newline without
    # intervening whitespace, e.g.: int\nBar
    args = re.sub('  +', ' ', args_strings.replace('\n', ' '))
    
  return args
  
def _CreateArgsNames(node):
  return [param.name for param in node.parameters]

def _GenerateMethods(output_lines, oncall_methods, ctors, dtors, source, class_node, full_class_name):
  function_type = ast.FUNCTION_VIRTUAL | ast.FUNCTION_PURE_VIRTUAL
  ctor_or_dtor = ast.FUNCTION_CTOR | ast.FUNCTION_DTOR
  indent = ' ' * _INDENT

  parent_method_definitions = []
  
  for node in class_node.body:
    # We only care about virtual functions.
    if (isinstance(node, ast.Function) and
        node.modifiers & function_type and
        not node.modifiers & ctor_or_dtor):
      # Pick out all the elements we need from the original function.
      const = ''
      if node.modifiers & ast.FUNCTION_CONST:
        const = 'CONST_'
      return_type = 'void'
      if node.return_type:
        # Add modifiers like 'const'.
        modifiers = ''
        if node.return_type.modifiers:
          modifiers = ' '.join(node.return_type.modifiers) + ' '
        return_type = modifiers + node.return_type.name
        template_args = [arg.name for arg in node.return_type.templated_types]
        if template_args:
          return_type += '<' + ', '.join(template_args) + '>'
          if len(template_args) > 1:
            for line in [
                '// The following line won\'t really compile, as the return',
                '// type has multiple template arguments.  To fix it, use a',
                '// typedef for the return type.']:
              output_lines.append(indent + line)
        if node.return_type.pointer:
          return_type += '*'
        if node.return_type.reference:
          return_type += '&'
      mock_method_macro = 'MOCK_%sMETHOD%d' % (const, len(node.parameters))

      args = _CreateArgs(node, source)

      if _PARTIAL:
        # Create parent method definition.
        # NOTE: doesn't work with unnamed parameters.
        args_names = _CreateArgsNames(node)
        args_names_strings = ', '.join(args_names)
      
        parent_method_name = 'Parent' + node.name
        return_statement = ''
        if node.return_type.name != 'void':
           return_statement = 'return '
        parent_method_definition = return_type + ' ' + parent_method_name + '(' + args + ') { ' + return_statement + class_node.name + '::' + node.name + '(' + args_names_strings + '); }'
        parent_method_definitions.extend([parent_method_definition])
      
        # Create ON_CALL statements that calls parent by default.
        oncall_method = 'ON_CALL(*this, ' + node.name + '(' + ('_, '*len(args_names))[0:-2] + ')).WillByDefault(Invoke(this, &' + full_class_name + '::' + parent_method_name + '));'
        oncall_methods.extend(['%s%s' % (indent*2, oncall_method)])
      
      # Create the mock method definition.
      output_lines.extend(['%s%s(%s,' % (indent, mock_method_macro, node.name),
                           '%s%s(%s));' % (indent*3, return_type, args)])

    if _PARTIAL:
      # Parse constructors and destructors.
      if node.modifiers & ast.FUNCTION_CTOR:
        args = _CreateArgs(node, source)
        args_names_strings = ', '.join(_CreateArgsNames(node))
        text = indent + full_class_name + '(' + args + ') :' + os.linesep
        text += (indent*2) + class_node.name + '(' + args_names_strings + ') {' + os.linesep
        text += (indent*2) + 'delegateMethodCallsToParent();' + os.linesep + indent + '}'
        ctors.append(text)
        
      if (isinstance(node, ast.Function) and
          node.modifiers & function_type and
          node.modifiers & ast.FUNCTION_DTOR):
        text = indent + 'virtual ~' + full_class_name + '() {}'
        dtors.append(text)
        pass

  if _PARTIAL:
    # Add parent method definitions.
    output_lines.extend([''])
    for parent_method_definition in parent_method_definitions:
      output_lines.extend(['%s%s' % (indent, parent_method_definition)])

def _GenerateMocks(filename, source, ast_list, desired_class_names):
  processed_class_names = set()
  lines = []
  oncall_methods = []
  ctors = []
  dtors = []
  
  for node in ast_list:
    if (isinstance(node, ast.Class) and node.body and
        # desired_class_names being None means that all classes are selected.
        (not desired_class_names or node.name in desired_class_names)):
      class_name = node.name
      full_class_name = ''
      if _PARTIAL:
        full_class_name = 'PartialMock' + class_name
      else:
        full_class_name = 'Mock' + class_name
      processed_class_names.add(class_name)
      class_node = node
      # Add namespace before the class.
      if class_node.namespace:
        lines.extend(['namespace %s {' % n for n in class_node.namespace])  # }
        lines.append('')

      # Add the class prolog.
      lines.append('class %s : public %s {' % (full_class_name, class_name))  # }
      lines.append('%spublic:' % (' ' * (_INDENT // 2)))

      # Add all the methods.
      _GenerateMethods(lines, oncall_methods, ctors, dtors, source, class_node, full_class_name)

      # Close the class.
      if lines:
        # If there are no virtual methods, no need for a public label.
        if len(lines) == 2:
          del lines[-1]
        elif _PARTIAL:
          # Add ON_CALL statements within the delegateMethodCallsToParent method.
          indent = (' ' * _INDENT)
          default_mock_constructor = os.linesep + indent + 'void delegateMethodCallsToParent() {' + os.linesep
          default_mock_constructor += os.linesep.join(oncall_methods)
          default_mock_constructor += os.linesep + indent + '}'
          lines.append(default_mock_constructor)
          
        lines.append(os.linesep)
          
        ctors_text = (os.linesep*2).join(ctors)
        lines.append(ctors_text)
        
        lines.append(os.linesep)
        
        dtors_text = (os.linesep*2).join(dtors)
        lines.append(dtors_text)
          
        # Only close the class if there really is a class.
        lines.append('};')
        lines.append('')  # Add an extra newline.
        
      # Close the namespace.
      if class_node.namespace:
        for i in range(len(class_node.namespace)-1, -1, -1):
          lines.append('}  // namespace %s' % class_node.namespace[i])
        lines.append('')  # Add an extra newline.

  if desired_class_names:
    missing_class_name_list = list(desired_class_names - processed_class_names)
    if missing_class_name_list:
      missing_class_name_list.sort()
      sys.stderr.write('Class(es) not found in %s: %s\n' %
                       (filename, ', '.join(missing_class_name_list)))
  elif not processed_class_names:
    sys.stderr.write('No class found in %s\n' % filename)

  return lines


def main():
  # Parse options.
  usage = '\nGoogle Mock Class Generator v%s\n\n' % '.'.join(map(str, _VERSION))
  usage += __doc__
  usage = usage.rstrip()

  parser = OptionParser(usage)
  parser.add_option('-p', '--partial', action='store_true', dest='partial', help='Generate a partial mock instead of just a mock (*NOTE* methods with unnamed parameters are unsupported and will cause problems.')
  
  (options, parsed_args) = parser.parse_args()
  
  # Set global flag for generated file type.
  global _PARTIAL
  if options.partial:
    _PARTIAL = True
  else:
    _PARTIAL = False
  
  # Create arguments, taking out any options.
  argv = [sys.argv[0]] + parsed_args

  if len(argv) < 2:
    parser.print_help()
    return 1

  global _INDENT
  try:
    _INDENT = int(os.environ['INDENT'])
  except KeyError:
    pass
  except:
    sys.stderr.write('Unable to use indent of %s\n' % os.environ.get('INDENT'))

  filename = argv[1]
  desired_class_names = None  # None means all classes in the source file.
  if len(argv) >= 3:
    desired_class_names = set(argv[2:])
  source = utils.ReadFile(filename)
  if source is None:
    return 1

  builder = ast.BuilderFromSource(source, filename)
  try:
    entire_ast = filter(None, builder.Generate())
  except KeyboardInterrupt:
    return
  except:
    # An error message was already printed since we couldn't parse.
    pass
  else:
    lines = _GenerateMocks(filename, source, entire_ast, desired_class_names)
    sys.stdout.write('\n'.join(lines))

if __name__ == '__main__':
  main(sys.argv)
