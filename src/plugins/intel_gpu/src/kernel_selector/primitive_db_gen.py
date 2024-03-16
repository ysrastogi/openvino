#!/usr/bin/python3
# Copyright (C) 2018-2024 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
# To add new kernel please add a .cl file to kernels directory
# the database name will be the part of the file name up to first '.' character
# the trailing characters are a tag to allow multiple primitive implementations

from __future__ import print_function
import os
import argparse
import glob
import ntpath
import re

class OpenCL2CHeaders(object):

    def __init__(self, kernels_folder, out_path, out_file_name_prim_db, out_file_name_batch_headers):
        self.kernels_folder = os.path.abspath(kernels_folder)
        self.out_path = os.path.abspath(out_path)
        self.out_file_name_prim_db = out_file_name_prim_db
        self.out_file_name_batch_headers = out_file_name_batch_headers
        self.include_files = {}
        self.batch_headers = []
        self.find_and_set_batch_headers()

    # NOTE: batch_headers are headers with macros on which the runtime jitter might depend on.
    # For example, fetch_data.cl defines GET_DATA_INDEX which is to be used to define macros generated by jitter.
    # These headers contains generally used macros and located under cl_kernels/include/batch_headers to be handled
    # specially for improving the jit compilation performance, i.e.,
    # they are not to be included in each kernel, but to be included only once at the beginning of each batch.
    def find_and_set_batch_headers(self):
        batch_headers_list = [ntpath.basename(h) for h in glob.glob(os.path.join(self.kernels_folder, "include/batch_headers/*.cl"))]
        deps = {}
        for h in batch_headers_list:
            header_file = os.path.abspath(os.path.join(self.kernels_folder, "include/batch_headers", h))
            f = open(header_file)
            content = f.readlines()
            deps[h] = {h}
            for line in content:
                if line.startswith('#include'):
                    include_file_name = line.strip().split('"')[1].strip()
                    deps[h].add(include_file_name)
                else:
                    continue
        while deps:
            self.topological_sort(next(iter(deps)), deps, [], self.batch_headers)

    def topological_sort(self, cur_key, items, stack, res):
        stack.append(cur_key)
        deps = [dep for dep in items[cur_key] if dep not in stack and dep not in res]
        for dep in deps:
            self.topological_sort(dep, items, stack, res)
        res.append(cur_key)
        items.pop(cur_key)
        stack.pop()

    def convert(self):
        res = '// This file is autogenerated by primitive_db_gen.py, all changes to this file will be undone\n\n'
        filelist = glob.glob(os.path.join(self.kernels_folder, "*.cl"))
        for filename in filelist:
            #try:
                print('processing {}'.format(filename))
                res += self.cl_file_to_str(filename)
            #except:
            #    pass
        out_file_name_prim_db = os.path.join(self.out_path, self.out_file_name_prim_db)
        with open(out_file_name_prim_db, 'w') as out_file:
            out_file.write(res)

        # write batch_header_str
        batch_headers = '// This file is autogenerated by primitive_db_gen.py, all changes to this file will be undone\n\n'
        batch_headers += self.batch_headers_to_str()
        out_file_name_batch_headers = os.path.join(self.out_path, self.out_file_name_batch_headers)
        with open(out_file_name_batch_headers, 'w') as out_file:
            out_file.write(batch_headers)

    def append_undefs(self, filename):
        undefs = ""
        content = []
        with open(filename) as f:
            content += f.readlines()
        for line in content:
            if '#define' in line:
                name = line.strip().split(" ")[1].split("(")[0]
                undefs += "#ifdef " + name + "\n"
                undefs += "#undef " + name + "\n"
                undefs += "#endif\n"
            if '# define' in line:
                name = line.strip().split(" ")[2].split("(")[0]
                undefs += "#ifdef " + name + "\n"
                undefs += "#undef " + name + "\n"
                undefs += "#endif\n"
        if filename in self.include_files:
            for include_file in self.include_files[filename]:
                include_file_undefs = self.append_undefs(include_file)
                undefs += include_file_undefs
        return undefs

    # Conservative removal of macro checking with its potential users
    # Potential users are determined by checking followings:
    #   - if macro is appearing on the line
    #   - if macro might be composed by concat
    def found_concat_user(self, words, start_idx, macro):
        potential_macro_user_exist = False
        concat_len = 0
        iter_idx = start_idx
        len_str1 = len_str2 = 0
        if words[iter_idx + 2] == "CAT":
            user_exist, len_str1 = self.found_concat_user(words, iter_idx + 2, macro)
            potential_macro_user_exist |= user_exist
        else:
            if macro.find(words[iter_idx + 2]) >= 0 :
                potential_macro_user_exist = True
            len_str1 = 1

        if words[iter_idx + 3 + len_str1] == "CAT":
            user_exist, len_str2 = self.found_concat_user(words, iter_idx + 3, macro)
            potential_macro_user_exist |= user_exist
        else:
            if macro.find(words[iter_idx + 3 + len_str1]) >= 0:
                potential_macro_user_exist = True
            len_str2 = 1
        return potential_macro_user_exist, (len_str1 + len_str2 + 4)

    def found_potential_macro_user(self, macro, contents_list):
        for line in contents_list:
            if line.find(macro) >= 0:
                return True
            if line.find("CAT") >= 0:
                words = ' '.join(re.split("(\W)", line)).split()
                iter_w = 0
                while iter_w < len(words):
                    if words[iter_w]  != "CAT":
                        iter_w += 1
                        continue
                    user_exist, concat_len = self.found_concat_user(words, iter_w, macro)
                    if user_exist:
                        return True
                    iter_w += concat_len
        return False

    def reduce_macros(self, contents):
        new_contents = ""
        contents_list = contents.split("\n")
        idx = 0
        while idx < len(contents_list):
            line = contents_list[idx]
            is_macro = re.search('#\s*define', line)
            macro = ""

            if is_macro:
                words = ' '.join(re.split("(\W)", line)).split()
                macro = words[words.index("define") + 1]

            if len(macro) == 0 or self.found_potential_macro_user(macro, contents_list):
                new_contents += (line.rstrip() + "\n")
                idx += 1
            else:
                if line.rstrip()[-1] == '\\':
                    while contents_list[idx].rstrip()[-1] == '\\':
                            idx += 1
                idx += 1
        return new_contents

    def append_file_content(self, filename, origin_file):
        res = ""
        content = []
        with open(filename) as f:
            content += f.readlines()

        optimize_includes = True
        for line in content:
            if line.startswith('#pragma'):
                if "enable_includes_optimization" in line:
                    optimize_includes = True
                elif "disable_includes_optimization" in line:
                    optimize_includes = False

            if line.startswith('#include'):
                include_file_name = line.strip().split('"')[1].strip()
                if ntpath.basename(include_file_name) in self.batch_headers:
                    continue
                full_path_include = os.path.abspath(os.path.join(os.path.dirname(filename), include_file_name))
                if full_path_include not in self.include_files[origin_file] or not optimize_includes:
                    self.include_files[origin_file][full_path_include] = True
                    res += self.append_file_content(full_path_include, origin_file)
                    res += "\n"
                continue
            res += '{}\n'.format(line.rstrip())
        if filename == origin_file:
            return self.reduce_macros(res)
        else:
            return res

    def batch_headers_to_str(self):
        max_lines = 200
        max_characters = 16350
        characters = 1  # Newline character above
        res = ""
        for h in self.batch_headers:
            res += '(std::string) R"(\n'
            header_file = os.path.abspath(os.path.join(os.path.dirname(self.kernels_folder + "/include/batch_headers"), "batch_headers/" + h))
            content = []
            with open(header_file) as f:
                content += f.readlines()
            for i, line in enumerate(content):
                if line.startswith('#include'):
                    continue
                if (i + 1) % max_lines == 0 or characters + len(line) + 1 > max_characters:
                    res += ')",' + ' (std::string) R"('
                    characters = 0
                res += '{}\n'.format(line.rstrip())
                characters += len(line) + 1
            res += ')",\n\n'
        return self.post_process_sources(res)

    def post_process_sources(self, content):
        comment_regexp = re.compile(r'(^)?[^\S\n]*/(?:\*(.*?)\*/[^\S\n]*|/[^\n]*)($)?', re.DOTALL | re.MULTILINE)

        def comment_replacer(match):
            begin, mid, end = match.group(1,2,3)
            if mid is None:
                return ''
            elif begin is not None or end is not None:
                return ''
            elif '\n' in mid:
                return '\n'
            else:
                return ' '

            return

        # Remove comments
        content = comment_regexp.sub(comment_replacer, content)
        # Remove empty lines
        content = os.linesep.join([s for s in content.splitlines() if s])
        # Remove multiple spaces
        content = content.replace("\\\n", "")
        content = re.sub(' +', ' ', content)

        return content

    def cl_file_to_str(self, filename):
        name = ntpath.basename(filename)
        self.include_files[filename] = {}
        kernel_name = name[:name.find('.cl')]
        res = '{{"{}",\n(std::string) R"__krnl(\n'.format(kernel_name)
        content = self.append_file_content(filename, filename)
        content += self.append_undefs(filename)
        max_lines = 200
        max_characters = 16350
        characters = 1  # Newline character above

        content = self.post_process_sources(content)

        for i, line in enumerate(content.split('\n')):
            if (i + 1) % max_lines == 0 or characters + len(line) + 1 > max_characters:
                res += ')__krnl"\n + R"__krnl('
                characters = 0
            res += line + '\n'
            characters += len(line) + 1

        res += ')__krnl"}},\n\n'.format(kernel_name)

        return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('-kernels', required=True, metavar='PATH', help='The absolute path to OpenCL kernels folder')
    ap.add_argument('-out_path', required=True, metavar='PATH', help='The absolute path to dump file')
    ap.add_argument('-out_file_name_prim_db', required=True, metavar='PATH', help='dump file name')
    ap.add_argument('-out_file_name_batch_headers', required=True, metavar='PATH', help='dump file name')
    args = ap.parse_args()

    converter = OpenCL2CHeaders(args.kernels, args.out_path, args.out_file_name_prim_db, args.out_file_name_batch_headers)
    converter.convert()

if __name__ == '__main__':
    main()
