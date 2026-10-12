import {test} from "node:test";
import assert from "node:assert/strict";
import {existsSync,readdirSync,readFileSync} from "node:fs";
import path from "node:path";
import ts from "typescript";

const sourceRoot=path.resolve("src");
const SDK_PACKAGE=/^(?:@[^/]+\/)?(?:metatrader5|mt5(?:-sdk|-api)?|exness(?:-sdk|-api)|oanda(?:-sdk|-api)|ctrader(?:-sdk|-api)|interactive-brokers-sdk)(?:\/|$)/i;

function filesUnder(directory){
 return readdirSync(directory,{withFileTypes:true}).flatMap(entry=>{
  const full=path.join(directory,entry.name);
  return entry.isDirectory()?[...filesUnder(full)]:/\.tsx?$/.test(entry.name)?[full]:[];
 });
}

function parse(source,name="fixture.tsx"){
 return ts.createSourceFile(name,source,ts.ScriptTarget.Latest,true,name.endsWith(".tsx")?ts.ScriptKind.TSX:ts.ScriptKind.TS);
}

function localModule(specifier,from){
 const base=specifier.startsWith("@/")?path.join(sourceRoot,specifier.slice(2)):specifier.startsWith(".")?path.resolve(path.dirname(from),specifier):null;
 if(!base)return null;
 for(const candidate of [base,`${base}.ts`,`${base}.tsx`,path.join(base,"index.ts"),path.join(base,"index.tsx")])if(existsSync(candidate))return candidate;
 return null;
}

function isLocalApiRoute(node){
 if(ts.isParenthesizedExpression(node))return isLocalApiRoute(node.expression);
 if(ts.isStringLiteral(node)||ts.isNoSubstitutionTemplateLiteral(node))return node.text.startsWith("/api/");
 if(ts.isBinaryExpression(node)&&node.operatorToken.kind===ts.SyntaxKind.PlusToken)
  return isLocalApiRoute(node.left);
 return ts.isTemplateExpression(node)&&node.head.text.startsWith("/api/");
}

function enclosingFunction(node){
 for(let parent=node.parent;parent;parent=parent.parent){
  if(ts.isFunctionDeclaration(parent))return {node:parent,name:parent.name?.text};
  if(ts.isArrowFunction(parent)||ts.isFunctionExpression(parent)){
   const declaration=parent.parent;
   const name=ts.isVariableDeclaration(declaration)&&ts.isIdentifier(declaration.name)?declaration.name.text:undefined;
   return {node:parent,name};
  }
 }
 return null;
}

function dynamicRouteIsBoundToLocalApi(call,argument,sourceFile){
 if(!ts.isIdentifier(argument))return false;
 const fn=enclosingFunction(call);
 if(!fn?.name)return false;
 const parameterIndex=fn.node.parameters.findIndex(parameter=>ts.isIdentifier(parameter.name)&&parameter.name.text===argument.text);
 if(parameterIndex<0)return false;
 let found=false,valid=true;
 function visit(node){
  if(ts.isCallExpression(node)&&ts.isIdentifier(node.expression)&&node.expression.text===fn.name){
   found=true;
   if(!node.arguments[parameterIndex]||!isLocalApiRoute(node.arguments[parameterIndex]))valid=false;
  }
  ts.forEachChild(node,visit);
 }
 visit(sourceFile);
 return found&&valid;
}

function auditBrowserModule(file,source){
 const sourceFile=parse(source,file);
 const violations=[];
 function visit(node){
  if(ts.isImportDeclaration(node)&&ts.isStringLiteral(node.moduleSpecifier)&&SDK_PACKAGE.test(node.moduleSpecifier.text))
   violations.push(`broker SDK import: ${node.moduleSpecifier.text}`);
  if(ts.isCallExpression(node)&&node.expression.kind===ts.SyntaxKind.ImportKeyword&&node.arguments[0]&&ts.isStringLiteral(node.arguments[0])&&SDK_PACKAGE.test(node.arguments[0].text))
   violations.push(`broker SDK import: ${node.arguments[0].text}`);
  const transport=ts.isCallExpression(node)&&ts.isIdentifier(node.expression)&&["fetch","EventSource","WebSocket"].includes(node.expression.text);
  const constructor=ts.isNewExpression(node)&&ts.isIdentifier(node.expression)&&["EventSource","WebSocket","XMLHttpRequest"].includes(node.expression.text);
  if((transport||constructor)&&node.expression&&ts.isIdentifier(node.expression)&&node.expression.text==="XMLHttpRequest")
   violations.push("XMLHttpRequest is outside the fixed same-origin API boundary");
  if((transport||constructor)&&node.arguments?.length){
   const target=node.arguments[0];
   const direct=ts.isStringLiteral(target)||ts.isNoSubstitutionTemplateLiteral(target)||ts.isTemplateExpression(target);
   if(direct&&!isLocalApiRoute(target))violations.push("network target is not a fixed same-origin /api route");
   if(!direct&&!dynamicRouteIsBoundToLocalApi(node,target,sourceFile))violations.push("dynamic network target is not bound to fixed same-origin /api call sites");
  }
  ts.forEachChild(node,visit);
 }
 visit(sourceFile);
 return violations;
}

function clientDependencyGraph(){
 const roots=filesUnder(sourceRoot).filter(file=>{
  const source=parse(readFileSync(file,"utf8"),file);
  return source.statements.some((statement,index)=>index===0&&ts.isExpressionStatement(statement)&&ts.isStringLiteral(statement.expression)&&statement.expression.text==="use client");
 });
 const visited=new Set(),queue=[...roots];
 while(queue.length){
  const file=queue.pop();
  if(visited.has(file))continue;
  visited.add(file);
  const source=parse(readFileSync(file,"utf8"),file);
  function visit(node){
   if(ts.isImportDeclaration(node)&&!node.importClause?.isTypeOnly&&ts.isStringLiteral(node.moduleSpecifier)){
    const local=localModule(node.moduleSpecifier.text,file);
    if(local)queue.push(local);
   }
   ts.forEachChild(node,visit);
  }
  visit(source);
 }
 return [...visited].sort();
}

test("browser boundary rejects direct broker SDK imports and remote transport targets",()=>{
 assert.deepEqual(auditBrowserModule("fixture.tsx",'import "mt5-sdk"; fetch("/api/orders");'),[
  "broker SDK import: mt5-sdk",
 ]);
 assert.deepEqual(auditBrowserModule("fixture.tsx",'fetch("https://broker.example/orders")'),[
  "network target is not a fixed same-origin /api route",
 ]);
 assert.deepEqual(auditBrowserModule("fixture.tsx",'new WebSocket("wss://broker.example")'),[
  "network target is not a fixed same-origin /api route",
 ]);
 assert.deepEqual(auditBrowserModule("fixture.tsx",'const target="https://broker.example"; fetch(target);'),[
  "dynamic network target is not bound to fixed same-origin /api call sites",
 ]);
 assert.deepEqual(auditBrowserModule("fixture.tsx",'fetch("https://broker.example" + "/api/orders")'),[
  "dynamic network target is not bound to fixed same-origin /api call sites",
 ]);
});

test("client modules and their local imports use only fixed same-origin API transports",()=>{
 const violations=[];
 for(const file of clientDependencyGraph()){
  for(const finding of auditBrowserModule(file,readFileSync(file,"utf8")))violations.push(`${path.relative(sourceRoot,file)}: ${finding}`);
 }
 assert.deepEqual(violations,[]);
});
